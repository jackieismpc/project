"""分段录制：每排一个 MP4。

调用方（试验引擎）在一排开始时 ``start_segment``，结束时 ``stop_segment``。采集线程只调用
:meth:`SegmentRecorder.offer`：把帧引用放进有界队列，队列满（帧数或字节数）就丢弃并计数，
**不阻塞采集**。缩放、编码、写盘都在单个写盘线程里完成。

文件流程：写 ``row_XX.part.mp4`` → ``VideoWriter.release`` → fsync → 改名为 ``row_XX.mp4``
→ 刷目录 → 计算 SHA-256。（临时文件保留 ``.mp4`` 结尾，因为 OpenCV 按扩展名选择封装格式，
``.mp4.part`` 在本机验证打不开。）已有文件绝不覆盖，重录同一排会得到 ``row_XX_02.mp4``。

**中文路径**：OpenCV 在 Windows 上打不开含非 ASCII 字符的路径。此时编码器先写到纯 ASCII 的
临时目录，封存时再移动到 ``.part.mp4``，之后的改名、哈希流程不变（见 ``paths.needs_ascii_path``）。

**断流处理**：采集线程调用 :meth:`abort_segment` 后，当前段在写盘线程里封存
（文件可播放、有哈希），记录标记 ``interrupted=True``，重连后不会自动续录；
调用方随后 ``stop_segment`` 取回记录。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import RecorderConfig
from gelinspect.services.video.errors import NoActiveSegmentError, RecorderError
from gelinspect.services.video.events import (
    EventSink,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)
from gelinspect.services.video.paths import (
    experiment_dir,
    fsync_directory,
    fsync_file,
    make_ascii_scratch_dir,
    needs_ascii_path,
    next_free_name,
    sha256_file,
    validate_experiment_id,
    validate_row_index,
)
from gelinspect.services.video.preview import scaled_size

logger = logging.getLogger("gelinspect.services.video.recorder")

_MAX_FINISHED = 32
_DROP_REPORT_INTERVAL_S = 5.0


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    """一个已封存的录制段。

    Attributes:
        experiment_id: 试验标识。
        row_index: 排号。
        path: 最终文件路径。
        sha256: 文件内容的 SHA-256（64 位小写十六进制）。
        size_bytes: 文件大小。
        frame_count: 实际写入的帧数。
        dropped_frames: 因写盘队列满而丢弃的录制帧数。
        source_gap_frames: 视频源帧序号跳号推算出的掉帧数（驱动层丢失）。
        duration_s: 首末帧采集时间之差（墙钟秒）。
        fps: 写入容器的帧率。
        width: 视频宽度。
        height: 视频高度。
        started_at: 首帧采集时间。
        ended_at: 末帧采集时间。
        interrupted: 是否被异常终止（断流、管线停止），True 时视频不完整。
        interrupt_reason: 终止原因。
    """

    experiment_id: str
    row_index: int
    path: Path
    sha256: str
    size_bytes: int
    frame_count: int
    dropped_frames: int
    source_gap_frames: int
    duration_s: float
    fps: float
    width: int
    height: int
    started_at: datetime
    ended_at: datetime
    interrupted: bool = False
    interrupt_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RecorderStats:
    """录制统计。

    Attributes:
        active: 是否有进行中的段。
        queued_frames: 写盘队列中的帧数。
        segments_finished: 已封存的段数。
        total_dropped_frames: 累计丢弃的录制帧数。
    """

    active: bool
    queued_frames: int
    segments_finished: int
    total_dropped_frames: int


def _even(value: int) -> int:
    return max(2, value - value % 2)


class _Segment:
    """一个进行中的录制段，由 :class:`SegmentRecorder` 管理。"""

    def __init__(
        self,
        *,
        experiment_id: str,
        row_index: int,
        part_path: Path,
        final_path: Path,
        writer_path: Path,
        scratch_dir: Path | None,
        writer: cv2.VideoWriter,
        width: int,
        height: int,
        fps: float,
        max_frames: int,
        max_bytes: int,
    ) -> None:
        self.experiment_id = experiment_id
        self.row_index = row_index
        self.part_path = part_path
        self.final_path = final_path
        self.writer_path = writer_path
        self.scratch_dir = scratch_dir
        self.writer = writer
        self.width = width
        self.height = height
        self.fps = fps
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.cond = threading.Condition()
        self.queue: deque[VideoFrame] = deque()
        self.queued_bytes = 0
        self.closing = False
        self.interrupt_reason: str | None = None
        self.dropped = 0
        self.gap = 0
        self.last_sequence: int | None = None
        self.written = 0
        self.first_at: datetime | None = None
        self.last_at: datetime | None = None
        self.error: Exception | None = None
        self.result: SegmentRecord | None = None
        self.failure: RecorderError | None = None
        self.done = threading.Event()
        self.thread: threading.Thread | None = None
        self.drop_reported = -_DROP_REPORT_INTERVAL_S


class SegmentRecorder:
    """分段录制器，同时是一个帧消费者（实现 ``offer``）。"""

    def __init__(
        self,
        data_dir: Path,
        config: RecorderConfig,
        *,
        clock: Clock | None = None,
        emit: EventSink | None = None,
    ) -> None:
        """创建录制器，不启动线程。

        Args:
            data_dir: 数据根目录，视频写到 ``<data_dir>/videos/<experiment_id>/``。
            config: 录制配置。
            clock: 时钟，用于事件时间戳。
            emit: 事件接收函数。
        """
        self._data_dir = data_dir
        self._config = config
        self._clock: Clock = clock or SystemClock()
        self._emit = emit
        self._lock = threading.Lock()
        self._segment: _Segment | None = None
        self._finished: list[_Segment] = []
        self._source_size: tuple[int, int] | None = None
        self._total_dropped = 0
        self._segments_finished = 0

    # ------------------------------------------------------------------ 配置

    def set_source_size(self, width: int, height: int) -> None:
        """设置视频源的实际分辨率，用于计算录制尺寸。

        Args:
            width: 视频源宽度。
            height: 视频源高度。
        """
        self._source_size = (width, height)

    def target_size(self) -> tuple[int, int]:
        """返回录制分辨率（长边不超过 ``long_side``，宽高取偶数）。

        Returns:
            ``(宽, 高)``。

        Raises:
            RecorderError: 尚未设置视频源分辨率。
        """
        if self._source_size is None:
            raise RecorderError("尚未得知视频源分辨率，请先启动管线")
        width, height = scaled_size(*self._source_size, self._config.long_side)
        return _even(width), _even(height)

    @property
    def active(self) -> bool:
        """是否有进行中的录制段。"""
        return self._segment is not None

    def stats(self) -> RecorderStats:
        """返回统计快照。

        Returns:
            :class:`RecorderStats`。
        """
        segment = self._segment
        queued = len(segment.queue) if segment is not None else 0
        with self._lock:
            return RecorderStats(
                active=segment is not None,
                queued_frames=queued,
                segments_finished=self._segments_finished,
                total_dropped_frames=self._total_dropped
                + (segment.dropped if segment is not None else 0),
            )

    # ------------------------------------------------------------------ 开始与结束

    def start_segment(
        self, experiment_id: str, row_index: int, *, fps: float | None = None
    ) -> Path:
        """开始录制一排。

        Args:
            experiment_id: 试验标识，会校验后才用于拼接路径。
            row_index: 排号 1–10。
            fps: 写入容器的帧率，None 时用配置的 ``default_fps``。

        Returns:
            录制完成后的最终文件路径（此时文件尚未生成）。

        Raises:
            InvalidIdentifierError: 标识或排号不合法。
            RecorderError: 已有进行中的段，或 ``VideoWriter`` 打不开。
        """
        validate_experiment_id(experiment_id)
        validate_row_index(row_index)
        width, height = self.target_size()
        rate = fps if fps is not None and fps > 0 else self._config.default_fps
        with self._lock:
            if self._segment is not None:
                raise RecorderError("已有进行中的录制段，请先 stop_segment")
            directory = experiment_dir(self._data_dir, "videos", experiment_id)
            name = next_free_name(directory, f"row_{row_index:02d}", ".mp4")
            final_path = directory / name
            part_path = directory / f"{final_path.stem}.part.mp4"
            # OpenCV 在 Windows 上打不开含中文的路径：此时先写到纯 ASCII 临时目录，
            # 收尾时再移到 part_path。
            scratch_dir = make_ascii_scratch_dir() if needs_ascii_path(part_path) else None
            writer_path = scratch_dir / "segment.mp4" if scratch_dir is not None else part_path
            fourcc = cv2.VideoWriter.fourcc(*self._config.fourcc)
            writer = cv2.VideoWriter(str(writer_path), fourcc, rate, (width, height))
            if not writer.isOpened():
                writer.release()
                part_path.unlink(missing_ok=True)
                if scratch_dir is not None:
                    shutil.rmtree(scratch_dir, ignore_errors=True)
                raise RecorderError(
                    f"无法创建视频写入器（编码 {self._config.fourcc}，{width}×{height}，"
                    f"{rate:.1f} fps，路径 {part_path}），请检查编码器和磁盘权限"
                )
            segment = _Segment(
                experiment_id=experiment_id,
                row_index=row_index,
                part_path=part_path,
                final_path=final_path,
                writer_path=writer_path,
                scratch_dir=scratch_dir,
                writer=writer,
                width=width,
                height=height,
                fps=rate,
                max_frames=self._config.queue_max_frames,
                max_bytes=self._config.queue_max_bytes,
            )
            segment.thread = threading.Thread(
                target=self._writer_loop,
                args=(segment,),
                name=f"video-writer-row{row_index:02d}",
                daemon=True,
            )
            segment.thread.start()
            self._segment = segment
        return final_path

    def stop_segment(self) -> SegmentRecord:
        """结束当前段，等待写盘线程收尾并返回记录。

        没有进行中的段时，返回最近一个已被 :meth:`abort_segment` 封存、尚未取走的记录。

        Returns:
            录制记录（路径、哈希、帧数、丢帧数、时长）。

        Raises:
            NoActiveSegmentError: 没有进行中也没有待取走的段。
            RecorderError: 段内没有任何帧、写盘出错，或写盘线程超时未结束。
        """
        with self._lock:
            segment = self._segment
            if segment is None:
                if not self._finished:
                    raise NoActiveSegmentError("没有进行中的录制段")
                segment = self._finished.pop()
        self._request_close(segment, None)
        if not segment.done.wait(self._config.finish_timeout_s):
            raise RecorderError(
                f"录制段 {segment.final_path.name} 收尾超时（{self._config.finish_timeout_s} 秒）"
            )
        with self._lock:
            if self._segment is segment:
                self._segment = None
            if segment in self._finished:
                self._finished.remove(segment)
        if segment.failure is not None:
            raise segment.failure
        assert segment.result is not None
        return segment.result

    def abort_segment(self, reason: str) -> None:
        """非阻塞地终止当前段（断流、管线停止时由采集线程调用）。

        段会在写盘线程里封存为不完整的记录（``interrupted=True``），之后由
        :meth:`stop_segment` 取回。没有进行中的段时无操作。

        Args:
            reason: 终止原因，写入记录和事件。
        """
        with self._lock:
            segment = self._segment
        if segment is not None:
            self._request_close(segment, reason)

    def close(self, timeout_s: float) -> bool:
        """终止进行中的段并等待全部写盘线程退出（管线停止时调用）。

        Args:
            timeout_s: 等待的最长时间。

        Returns:
            写盘线程是否都已退出。
        """
        self.abort_segment("pipeline_stopped")
        with self._lock:
            segments = [s for s in (self._segment, *self._finished) if s is not None]
        return all(s.done.wait(timeout_s) for s in segments)

    def _request_close(self, segment: _Segment, reason: str | None) -> None:
        with segment.cond:
            if reason is not None and segment.interrupt_reason is None and not segment.closing:
                segment.interrupt_reason = reason
            segment.closing = True
            segment.cond.notify_all()
        if reason is not None:
            self._finish_bookkeeping(segment)

    def _finish_bookkeeping(self, segment: _Segment) -> None:
        """把被异常终止的段从「进行中」移到「待取走」列表。"""
        with self._lock:
            if self._segment is segment:
                self._segment = None
                self._finished.append(segment)
                del self._finished[:-_MAX_FINISHED]

    # ------------------------------------------------------------------ 采集线程入口

    def offer(self, frame: VideoFrame) -> None:
        """提交一帧，由采集线程调用。非阻塞，队列满时丢帧并计数。

        Args:
            frame: 视频帧。
        """
        segment = self._segment
        if segment is None:
            return
        nbytes = int(frame.image.nbytes)
        report = False
        with segment.cond:
            if segment.closing:
                return
            last = segment.last_sequence
            if last is not None and frame.sequence > last + 1:
                segment.gap += frame.sequence - last - 1
            segment.last_sequence = frame.sequence
            if (
                len(segment.queue) >= segment.max_frames
                or segment.queued_bytes + nbytes > segment.max_bytes
            ):
                segment.dropped += 1
                now = self._clock.monotonic()
                if now - segment.drop_reported >= _DROP_REPORT_INTERVAL_S:
                    segment.drop_reported = now
                    report = True
                dropped = segment.dropped
            else:
                segment.queue.append(frame)
                segment.queued_bytes += nbytes
                segment.cond.notify()
                dropped = 0
        if report and self._emit is not None:
            self._emit(
                make_event(
                    self._clock,
                    VideoEventCode.RECORDING_DROPPED,
                    VideoEventLevel.WARNING,
                    f"录制队列已满，第 {segment.row_index} 排累计丢弃 {dropped} 帧录制帧",
                    value=float(dropped),
                )
            )

    # ------------------------------------------------------------------ 写盘线程

    def _writer_loop(self, segment: _Segment) -> None:
        try:
            self._consume(segment)
            self._finalize(segment)
        except Exception as exc:
            logger.exception("录制写盘线程异常")
            segment.failure = RecorderError(f"录制失败：{exc!r}")
            self._discard_part(segment)
        finally:
            if segment.scratch_dir is not None:
                shutil.rmtree(segment.scratch_dir, ignore_errors=True)
            with self._lock:
                if self._segment is segment:
                    self._segment = None
                    self._finished.append(segment)
                    del self._finished[:-_MAX_FINISHED]
                self._total_dropped += segment.dropped
                self._segments_finished += 1
            segment.done.set()
            if segment.interrupt_reason is not None and self._emit is not None:
                self._emit(
                    make_event(
                        self._clock,
                        VideoEventCode.SEGMENT_INTERRUPTED,
                        VideoEventLevel.ERROR,
                        f"第 {segment.row_index} 排录制被中断（{segment.interrupt_reason}），"
                        f"已封存 {segment.written} 帧：{segment.final_path.name}",
                        value=float(segment.written),
                    )
                )

    def _consume(self, segment: _Segment) -> None:
        target = (segment.width, segment.height)
        while True:
            with segment.cond:
                while not segment.queue and not segment.closing:
                    segment.cond.wait()
                if not segment.queue:
                    return
                frame = segment.queue.popleft()
                segment.queued_bytes -= int(frame.image.nbytes)
            if segment.error is not None:
                continue  # 出错后丢弃剩余帧，直到队列清空
            try:
                image = frame.image
                if (image.shape[1], image.shape[0]) != target:
                    image = cast(
                        "NDArray[np.uint8]",
                        cv2.resize(image, target, interpolation=cv2.INTER_AREA),
                    )
                segment.writer.write(image)
            except Exception as exc:
                segment.error = exc
                with segment.cond:
                    segment.closing = True
                continue
            segment.written += 1
            if segment.first_at is None:
                segment.first_at = frame.captured_at
            segment.last_at = frame.captured_at

    def _finalize(self, segment: _Segment) -> None:
        segment.writer.release()
        if segment.error is not None:
            self._discard_part(segment)
            segment.failure = RecorderError(f"写入视频帧失败：{segment.error!r}")
            return
        if segment.written == 0 or segment.first_at is None or segment.last_at is None:
            self._discard_part(segment)
            segment.failure = RecorderError(
                f"第 {segment.row_index} 排录制段内没有任何帧，未生成文件"
            )
            return
        if segment.writer_path != segment.part_path:
            shutil.move(segment.writer_path, segment.part_path)
        size = segment.part_path.stat().st_size if segment.part_path.exists() else 0
        if size <= 0:
            self._discard_part(segment)
            segment.failure = RecorderError(f"视频写入器产出了空文件：{segment.part_path.name}")
            return
        fsync_file(segment.part_path)
        os.replace(segment.part_path, segment.final_path)
        fsync_directory(segment.final_path.parent)
        digest = sha256_file(segment.final_path)
        segment.result = SegmentRecord(
            experiment_id=segment.experiment_id,
            row_index=segment.row_index,
            path=segment.final_path,
            sha256=digest,
            size_bytes=segment.final_path.stat().st_size,
            frame_count=segment.written,
            dropped_frames=segment.dropped,
            source_gap_frames=segment.gap,
            duration_s=(segment.last_at - segment.first_at).total_seconds(),
            fps=segment.fps,
            width=segment.width,
            height=segment.height,
            started_at=segment.first_at,
            ended_at=segment.last_at,
            interrupted=segment.interrupt_reason is not None,
            interrupt_reason=segment.interrupt_reason,
        )

    @staticmethod
    def _discard_part(segment: _Segment) -> None:
        try:
            segment.writer.release()
        except Exception:
            logger.debug("释放 VideoWriter 失败", exc_info=True)
        segment.part_path.unlink(missing_ok=True)
        segment.writer_path.unlink(missing_ok=True)
