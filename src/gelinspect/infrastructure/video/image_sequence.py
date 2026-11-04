"""图片序列视频源与整排图片读取。

- :class:`ImageSequenceSource`：把文件夹内的 JPG/PNG 按自然排序、按配置 FPS 当作帧流播放，可循环。
  ``open`` 会解码全部图片做校验（分辨率必须一致、文件不得损坏），所以启动成本与图片数量成正比。
  播放结束时 ``read`` 返回 None（``is_live=False``）。
- :func:`load_row_capture`：直接读取一排的翻转前后图片，不经过环形缓冲，结果可直接组装
  ``RowAnalysisRequest``。目录约定 ``<dir>/before/*.png|jpg`` 与 ``<dir>/after/*.png|jpg``。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gelinspect.infrastructure.video.image_io import ImageLoadError, list_images, read_image
from gelinspect.interfaces.analysis import MIN_FRAMES_PER_SIDE, CapturedFrame
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)

DEFAULT_SEQUENCE_FPS = 10.0
_MAX_CATCH_UP_S = 1.0


class ImageSequenceSource:
    """``VideoSource`` 的图片序列实现，可无参构造。"""

    def __init__(
        self,
        *,
        loop: bool = False,
        realtime: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """创建视频源，不读取文件。

        Args:
            loop: 播完后是否从头播放，帧序号继续递增。
            realtime: 是否按 FPS 限速；False 时尽快产出。
            sleep: 等待函数。
        """
        self._loop = loop
        self._realtime = realtime
        self._sleep = sleep
        self._files: list[Path] = []
        self._index = 0
        self._sequence = 0
        self._interval = 1.0 / DEFAULT_SEQUENCE_FPS
        self._next_due = 0.0
        self._opened = False

    def open(self, config: VideoConfig) -> VideoInfo:
        """校验并打开图片文件夹。

        Args:
            config: ``device`` 为文件夹路径；``fps`` 为播放帧率，默认 10。

        Returns:
            实际参数，``is_live`` 为 False，``frame_count`` 为图片数。

        Raises:
            VideoOpenError: 目录不存在、没有图片、有损坏的图片，或分辨率不一致。
        """
        self.close()
        directory = Path(config.device)
        try:
            files = list_images(directory)
        except ImageLoadError as exc:
            raise VideoOpenError(str(exc)) from exc
        if not files:
            raise VideoOpenError(f"目录里没有 JPG/PNG 图片：{directory}")
        size: tuple[int, int] | None = None
        for path in files:
            try:
                shape = read_image(path).shape
            except ImageLoadError as exc:
                raise VideoOpenError(str(exc)) from exc
            current = (shape[1], shape[0])
            if size is None:
                size = current
            elif current != size:
                raise VideoOpenError(
                    f"图片分辨率不一致：{path.name} 为 {current[0]}×{current[1]}，"
                    f"前面的图片为 {size[0]}×{size[1]}"
                )
        assert size is not None
        fps = config.fps or DEFAULT_SEQUENCE_FPS
        self._files, self._index, self._sequence = files, 0, 0
        self._interval = 1.0 / fps
        self._next_due = time.monotonic()
        self._opened = True
        return VideoInfo(
            width=size[0],
            height=size[1],
            fps=fps,
            is_live=False,
            frame_count=len(files),
            description=f"图片序列 {directory.name}（{len(files)} 张）",
        )

    def read(self) -> VideoFrame | None:
        """读取下一张图片。

        Returns:
            下一帧；播完且不循环时返回 None。

        Raises:
            VideoNotOpenError: 未打开或已关闭。
            VideoDisconnectedError: 打开后文件被删除、改坏或分辨率被改动。
        """
        if not self._opened:
            raise VideoNotOpenError("图片序列未打开")
        if self._index >= len(self._files):
            if not self._loop:
                return None
            self._index = 0
        path = self._files[self._index]
        try:
            image = read_image(path)
        except ImageLoadError as exc:
            raise VideoDisconnectedError(f"图片在打开后变得不可读：{exc}") from exc
        self._index += 1
        if self._realtime:
            now = time.monotonic()
            wait = self._next_due - now
            if wait > 0:
                self._sleep(wait)
            self._next_due = max(self._next_due + self._interval, now - _MAX_CATCH_UP_S)
        frame = VideoFrame(
            image=image,
            sequence=self._sequence,
            captured_at=datetime.now(UTC),
            monotonic_s=time.monotonic(),
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        """关闭，可重复调用。"""
        self._opened = False
        self._files = []


@dataclass(frozen=True, slots=True)
class RowCapture:
    """一排的翻转前后图片。

    Attributes:
        frames_before: 翻转前的帧，按文件自然排序。
        frames_after: 翻转后的帧，按文件自然排序。
    """

    frames_before: list[CapturedFrame]
    frames_after: list[CapturedFrame]


_FRAME_SPACING_S = 0.1
_PHASE_GAP_S = 10.0


def load_row_capture(directory: Path, *, min_frames: int = MIN_FRAMES_PER_SIDE) -> RowCapture:
    """读取 ``<directory>/before`` 与 ``<directory>/after`` 下的一排图片。

    每组至少 ``min_frames`` 张（算法接口要求 n≥3），所有图片分辨率必须一致。照片没有可靠的采集
    时间，``captured_at`` 按「最早的文件修改时间 + 序号 × 0.1 秒」合成，翻转后的第一张比翻转前的
    最后一张晚 10 秒，保证满足 ``RowAnalysisRequest`` 的时间顺序要求；``source_path`` 指向原文件。

    Args:
        directory: 一排的目录。
        min_frames: 每组最少帧数。

    Returns:
        :class:`RowCapture`。

    Raises:
        ImageLoadError: 目录或子目录缺失、某组不足 ``min_frames`` 张、图片损坏或分辨率不一致。
    """
    groups: dict[str, list[Path]] = {}
    for name in ("before", "after"):
        files = list_images(directory / name)
        if len(files) < min_frames:
            raise ImageLoadError(
                f"{directory / name} 至少需要 {min_frames} 张图片，实际只有 {len(files)} 张"
            )
        groups[name] = files
    base = datetime.fromtimestamp(
        min(p.stat().st_mtime for files in groups.values() for p in files), UTC
    )
    size: tuple[int, int] | None = None

    def load(files: list[Path], start: datetime) -> list[CapturedFrame]:
        nonlocal size
        frames: list[CapturedFrame] = []
        for i, path in enumerate(files):
            image = read_image(path)
            current = (image.shape[1], image.shape[0])
            if size is None:
                size = current
            elif current != size:
                raise ImageLoadError(
                    f"图片分辨率不一致：{path} 为 {current[0]}×{current[1]}，"
                    f"其他图片为 {size[0]}×{size[1]}"
                )
            image.flags.writeable = False
            frames.append(
                CapturedFrame(
                    image=image,
                    captured_at=start + timedelta(seconds=i * _FRAME_SPACING_S),
                    source_path=path,
                )
            )
        return frames

    before = load(groups["before"], base)
    after_start = before[-1].captured_at + timedelta(seconds=_PHASE_GAP_S)
    return RowCapture(frames_before=before, frames_after=load(groups["after"], after_start))
