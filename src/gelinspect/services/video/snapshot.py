"""抓帧：翻转前后各取 n 帧，原图落盘，组装成 ``CapturedFrame``。

流程：

1. 翻转开始时调用 :meth:`SnapshotGrabber.mark_flip_start`，缓冲保护翻转前最近 n 帧（只保护这 n 帧，
   不保护翻转过程中的帧，否则 12MP 下会固定占用数 GB 内存）。
2. 翻转结束后调用 :meth:`SnapshotGrabber.grab`：翻转前取「时间早于翻转开始」的最近 n 帧；
   翻转后等到「翻转结束时间 + 稳定时间」之后，取最早的 n 帧。不足 n 帧时一直等到超时，
   然后抛 :class:`InsufficientFramesError`，**不会静默少帧**。
3. 原图无损 PNG（或 JPG）编码后写盘，在工作线程池中并行完成，不占用采集线程。
   先写 ``.part`` 再 fsync、改名，同时算 SHA-256。

时间约定：``flip_started_at``、``flip_completed_at`` 与 ``VideoFrame.captured_at``
使用同一个本机时钟（即 ``FlipResult.started_at`` 和 ``completed_at``）。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import cv2

from gelinspect.interfaces.analysis import CapturedFrame
from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.config import SnapshotConfig
from gelinspect.services.video.errors import (
    InsufficientFramesError,
    SnapshotError,
)
from gelinspect.services.video.paths import (
    experiment_dir,
    fsync_directory,
    next_free_name,
    validate_experiment_id,
    validate_row_index,
)
from gelinspect.services.video.ring_buffer import FrameRingBuffer

logger = logging.getLogger("gelinspect.services.video.snapshot")

Phase = Literal["before", "after"]


@dataclass(frozen=True, slots=True)
class SavedImage:
    """一张已落盘的原图。

    Attributes:
        phase: ``before`` 为翻转前，``after`` 为翻转后。
        index: 同一阶段内的序号，从 1 开始。
        path: 文件路径。
        sha256: 文件内容的 SHA-256。
        size_bytes: 文件大小。
        sequence: 帧的视频源序号。
        captured_at: 帧的采集时间。
    """

    phase: Phase
    index: int
    path: Path
    sha256: str
    size_bytes: int
    sequence: int
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """一次抓帧的结果。

    Attributes:
        experiment_id: 试验标识。
        row_index: 排号。
        directory: 图像目录。
        frames_before: 翻转前的帧，按时间升序，``source_path`` 已填写。
        frames_after: 翻转后的帧，按时间升序，``source_path`` 已填写。
        files: 全部落盘文件（先翻转前、后翻转后）。
        flip_started_at: 翻转开始时间。
        flip_completed_at: 翻转结束时间。
        settle_s: 使用的稳定时间。
    """

    experiment_id: str
    row_index: int
    directory: Path
    frames_before: list[CapturedFrame]
    frames_after: list[CapturedFrame]
    files: list[SavedImage]
    flip_started_at: datetime
    flip_completed_at: datetime
    settle_s: float


@dataclass(slots=True)
class _Mark:
    started_at: datetime
    pin_id: int | None


class SnapshotGrabber:
    """抓帧器。"""

    def __init__(
        self,
        buffer: FrameRingBuffer,
        data_dir: Path,
        config: SnapshotConfig,
    ) -> None:
        """创建抓帧器，线程池按需创建。

        Args:
            buffer: 环形缓冲。
            data_dir: 数据根目录，图像写到 ``<data_dir>/images/<experiment_id>/row_XX/``。
            config: 抓帧配置。
        """
        self._buffer = buffer
        self._data_dir = data_dir
        self._config = config
        self._lock = threading.Lock()
        self._marks: dict[int | None, _Mark] = {}
        self._io_pool: ThreadPoolExecutor | None = None
        self._job_pool: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------------ 翻转标记

    def mark_flip_start(
        self, started_at: datetime, row_index: int | None = None, *, n: int | None = None
    ) -> None:
        """标记翻转开始，并保护翻转前最近 n 帧不被淘汰。

        同一 ``row_index`` 重复标记时，旧标记作废。

        Args:
            started_at: 翻转开始时间（带时区）。
            row_index: 排号；None 表示不区分排，由下一次 :meth:`grab` 使用。
            n: 保护的帧数，默认 ``frames_per_side``。
        """
        if started_at.tzinfo is None:
            raise ValueError("started_at 必须带时区")
        count = n if n is not None else self._config.frames_per_side
        pin_id = self._buffer.pin_last_before(started_at, count)
        with self._lock:
            old = self._marks.pop(row_index, None)
            self._marks[row_index] = _Mark(started_at, pin_id)
        if old is not None and old.pin_id is not None:
            self._buffer.unpin(old.pin_id)

    def cancel_mark(self, row_index: int | None = None) -> None:
        """放弃一个翻转标记并解除保护（翻转失败时调用）。

        Args:
            row_index: 排号，需与 :meth:`mark_flip_start` 一致。
        """
        with self._lock:
            mark = self._marks.pop(row_index, None)
        if mark is not None and mark.pin_id is not None:
            self._buffer.unpin(mark.pin_id)

    # ------------------------------------------------------------------ 抓帧

    def grab(
        self,
        row_index: int,
        n: int,
        settle_s: float | None = None,
        *,
        experiment_id: str,
        flip_completed_at: datetime,
        flip_started_at: datetime | None = None,
        timeout_s: float | None = None,
        image_format: Literal["png", "jpg"] | None = None,
    ) -> SnapshotResult:
        """取翻转前后各 n 帧并落盘，阻塞直到完成。

        Args:
            row_index: 排号 1–10。
            n: 翻转前后各取多少帧。
            settle_s: 稳定时间，None 时用配置值。
            experiment_id: 试验标识，会校验后才用于拼接路径。
            flip_completed_at: 翻转结束时间（``FlipResult.completed_at``）。
            flip_started_at: 翻转开始时间；None 时使用 :meth:`mark_flip_start` 登记的时间。
            timeout_s: 等待翻转后帧的最长时间，None 时用配置值。
            image_format: ``png`` 或 ``jpg``，None 时用配置值。

        Returns:
            抓帧结果。

        Raises:
            InvalidIdentifierError: 标识或排号不合法。
            InsufficientFramesError: 翻转前或翻转后的帧不足 n 帧。
            SnapshotError: 缺少翻转开始时间、管线被停止或写盘失败。
        """
        validate_experiment_id(experiment_id)
        validate_row_index(row_index)
        return self._grab(
            self._take_mark(row_index),
            row_index,
            n,
            settle_s,
            experiment_id,
            flip_completed_at,
            flip_started_at,
            timeout_s,
            image_format,
        )

    def _grab(
        self,
        mark: _Mark | None,
        row_index: int,
        n: int,
        settle_s: float | None,
        experiment_id: str,
        flip_completed_at: datetime,
        flip_started_at: datetime | None,
        timeout_s: float | None,
        image_format: Literal["png", "jpg"] | None,
    ) -> SnapshotResult:
        if n < 1:
            raise ValueError("n 必须不小于 1")
        started = flip_started_at or (mark.started_at if mark is not None else None)
        pin_id: int | None = mark.pin_id if mark is not None else None
        after_pin: int | None = None
        try:
            if started is None:
                raise SnapshotError("缺少翻转开始时间：请先 mark_flip_start 或传入 flip_started_at")
            settle = self._config.settle_s if settle_s is None else settle_s
            wait = self._config.wait_timeout_s if timeout_s is None else timeout_s
            fmt = image_format or self._config.image_format

            before = self._buffer.frames_before(started, n)
            if len(before) < n:
                raise InsufficientFramesError(
                    "before", n, len(before), "缓冲中翻转开始之前的帧不够，或已被淘汰"
                )
            threshold = flip_completed_at + timedelta(seconds=settle)
            after_pin = self._buffer.pin(threshold)
            after, cancelled = self._buffer.wait_for_frames_from(threshold, n, wait)
            if len(after) < n:
                detail = "管线已停止" if cancelled else f"等待 {wait:g} 秒超时"
                raise InsufficientFramesError("after", n, len(after), detail)
            return self._save(
                experiment_id, row_index, before, after, fmt, started, flip_completed_at, settle
            )
        finally:
            if after_pin is not None:
                self._buffer.unpin(after_pin)
            if pin_id is not None:
                self._buffer.unpin(pin_id)

    def grab_async(
        self,
        row_index: int,
        n: int,
        settle_s: float | None = None,
        *,
        experiment_id: str,
        flip_completed_at: datetime,
        flip_started_at: datetime | None = None,
        timeout_s: float | None = None,
        image_format: Literal["png", "jpg"] | None = None,
    ) -> Future[SnapshotResult]:
        """在工作线程中抓帧，立即返回 ``Future``。参数同 :meth:`grab`。

        翻转标记在提交时就被取走，因此提交后可以立即对下一排调用 :meth:`mark_flip_start`。

        Returns:
            结果为 :class:`SnapshotResult` 的 ``Future``，失败时携带 :meth:`grab` 的异常。
        """
        validate_experiment_id(experiment_id)
        validate_row_index(row_index)
        mark = self._take_mark(row_index)
        return self._jobs().submit(
            self._grab,
            mark,
            row_index,
            n,
            settle_s,
            experiment_id,
            flip_completed_at,
            flip_started_at,
            timeout_s,
            image_format,
        )

    def close(self) -> None:
        """解除全部保护并关闭线程池（等待进行中的任务结束）。"""
        with self._lock:
            marks = list(self._marks.values())
            self._marks.clear()
            io_pool, job_pool = self._io_pool, self._job_pool
            self._io_pool = self._job_pool = None
        for mark in marks:
            if mark.pin_id is not None:
                self._buffer.unpin(mark.pin_id)
        if job_pool is not None:
            job_pool.shutdown(wait=True)
        if io_pool is not None:
            io_pool.shutdown(wait=True)

    # ------------------------------------------------------------------ 内部

    def _take_mark(self, row_index: int) -> _Mark | None:
        with self._lock:
            mark = self._marks.pop(row_index, None)
            if mark is None:
                mark = self._marks.pop(None, None)
            return mark

    def _jobs(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._job_pool is None:
                self._job_pool = ThreadPoolExecutor(2, thread_name_prefix="video-grab")
            return self._job_pool

    def _io(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._io_pool is None:
                self._io_pool = ThreadPoolExecutor(
                    self._config.io_workers, thread_name_prefix="video-imgio"
                )
            return self._io_pool

    def _save(
        self,
        experiment_id: str,
        row_index: int,
        before: Sequence[VideoFrame],
        after: Sequence[VideoFrame],
        fmt: Literal["png", "jpg"],
        started: datetime,
        completed: datetime,
        settle: float,
    ) -> SnapshotResult:
        base = experiment_dir(self._data_dir, "images", experiment_id)
        directory = base / next_free_name(base, f"row_{row_index:02d}", "")
        directory.mkdir()
        jobs: list[tuple[Phase, int, VideoFrame, Path]] = []
        phases: list[tuple[Phase, Sequence[VideoFrame]]] = [("before", before), ("after", after)]
        for phase, frames in phases:
            for index, frame in enumerate(frames, start=1):
                jobs.append((phase, index, frame, directory / f"{phase}_{index:02d}.{fmt}"))
        pool = self._io()
        futures = [pool.submit(self._write_one, fmt, *job) for job in jobs]
        saved: list[SavedImage] = []
        error: Exception | None = None
        for future in futures:
            try:
                saved.append(future.result())
            except Exception as exc:
                error = error or exc
        if error is not None:
            for _, _, _, path in jobs:
                path.unlink(missing_ok=True)
                Path(f"{path}.part").unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                directory.rmdir()
            raise SnapshotError(f"原图写盘失败：{error!r}") from error
        fsync_directory(directory)
        paths = {(item.phase, item.index): item.path for item in saved}
        return SnapshotResult(
            experiment_id=experiment_id,
            row_index=row_index,
            directory=directory,
            frames_before=self._captured(before, "before", paths),
            frames_after=self._captured(after, "after", paths),
            files=saved,
            flip_started_at=started,
            flip_completed_at=completed,
            settle_s=settle,
        )

    @staticmethod
    def _captured(
        frames: Sequence[VideoFrame], phase: Phase, paths: dict[tuple[Phase, int], Path]
    ) -> list[CapturedFrame]:
        return [
            CapturedFrame(
                image=frame.image,
                captured_at=frame.captured_at,
                source_path=paths[(phase, index)],
            )
            for index, frame in enumerate(frames, start=1)
        ]

    def _write_one(
        self, fmt: Literal["png", "jpg"], phase: Phase, index: int, frame: VideoFrame, path: Path
    ) -> SavedImage:
        """编码并写盘一张图，返回含哈希的记录。"""
        if fmt == "png":
            params = [cv2.IMWRITE_PNG_COMPRESSION, self._config.png_compression]
        else:
            params = [cv2.IMWRITE_JPEG_QUALITY, self._config.jpeg_quality]
        ok, encoded = cv2.imencode(f".{fmt}", frame.image, params)
        if not ok:
            raise SnapshotError(f"图像编码失败：{path.name}")
        data = encoded.tobytes()
        part = Path(f"{path}.part")
        with part.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, path)
        return SavedImage(
            phase=phase,
            index=index,
            path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            sequence=frame.sequence,
            captured_at=frame.captured_at,
        )
