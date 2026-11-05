"""视频文件回放源，用于调试和演示。

- 默认按文件帧率限速（``realtime=True``），帧的时间戳是回放时的真实时间，
  所以和设备的翻转时间可以对齐；``realtime=False`` 时尽快读完。
- ``loop=True`` 时到达末尾后从头播放，帧序号继续递增。
- 文件播放结束时 ``read`` 返回 None（``is_live=False`` 的约定），视频管线据此发出结束事件。
- Windows 下 OpenCV 打不开含中文的路径：此时先把文件链接或复制到纯 ASCII 临时目录再打开，
  关闭时删除（见 ``_ascii_path``）。
- 配置中的 ``width``、``height`` 被忽略，分辨率以文件为准；``fps`` 在限速时覆盖文件帧率。
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from gelinspect.infrastructure.video._ascii_path import (
    discard_scratch,
    link_to_ascii_path,
    needs_ascii_path,
)
from gelinspect.infrastructure.video._image import normalize_bgr
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)

logger = logging.getLogger("gelinspect.infrastructure.video.file_source")

DEFAULT_FILE_FPS = 25.0
_MAX_CATCH_UP_S = 1.0


class FileVideoSource:
    """``VideoSource`` 的文件回放实现，可无参构造。"""

    def __init__(
        self,
        *,
        realtime: bool = True,
        loop: bool = False,
        speed: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """创建视频源，不打开文件。

        Args:
            realtime: 是否按帧率限速。
            loop: 到达末尾后是否从头播放。
            speed: 限速时的播放倍速，必须为正。
            sleep: 等待函数，测试时可替换。

        Raises:
            ValueError: ``speed`` 不为正。
        """
        if speed <= 0:
            raise ValueError("speed 必须为正")
        self._realtime = realtime
        self._loop = loop
        self._speed = speed
        self._sleep = sleep
        self._cap: cv2.VideoCapture | None = None
        self._interval = 1.0 / DEFAULT_FILE_FPS
        self._next_due = 0.0
        self._sequence = 0
        self._scratch: Path | None = None

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开视频文件。

        Args:
            config: 配置，``device`` 为文件路径。

        Returns:
            实际参数，``is_live`` 为 False。

        Raises:
            VideoOpenError: 文件不存在、无法解码或没有任何帧。
        """
        self.close()
        path = Path(config.device)
        if not path.is_file():
            raise VideoOpenError(f"视频文件不存在：{path}")
        open_path = path
        if needs_ascii_path(path):
            try:
                open_path = self._scratch = link_to_ascii_path(path)
            except OSError as exc:
                raise VideoOpenError(f"路径含非 ASCII 字符，无法交给 OpenCV：{exc}") from exc
        cap = cv2.VideoCapture(str(open_path))
        if not cap.isOpened():
            cap.release()
            self._discard_scratch()
            raise VideoOpenError(f"无法打开或解码视频文件：{path}")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            ok, probe = cap.read()
            if not ok or not isinstance(probe, np.ndarray):
                cap.release()
                self._discard_scratch()
                raise VideoOpenError(f"视频文件里没有可读的帧：{path}")
            height, width = probe.shape[:2]
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        file_fps = cap.get(cv2.CAP_PROP_FPS)
        fps = file_fps if math.isfinite(file_fps) and file_fps > 0 else None
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        pace_fps = config.fps or fps or DEFAULT_FILE_FPS
        self._cap = cap
        self._interval = 1.0 / (pace_fps * self._speed)
        self._next_due = time.monotonic()
        self._sequence = 0
        return VideoInfo(
            width=width,
            height=height,
            fps=fps,
            is_live=False,
            frame_count=count if count > 0 else None,
            description=f"视频文件 {path.name}",
        )

    def read(self) -> VideoFrame | None:
        """读取下一帧，限速时阻塞到该帧的播放时刻。

        Returns:
            下一帧；到达文件末尾（且不循环）时返回 None。

        Raises:
            VideoNotOpenError: 未打开或已关闭。
        """
        cap = self._cap
        if cap is None:
            raise VideoNotOpenError("视频文件未打开")
        ok, raw = cap.read()
        if not ok and self._loop and self._sequence > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, raw = cap.read()
        image = normalize_bgr(raw) if ok else None
        if image is None:
            return None
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
        """关闭文件，可重复调用。"""
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()
        self._discard_scratch()

    def _discard_scratch(self) -> None:
        scratch, self._scratch = self._scratch, None
        if scratch is not None:
            discard_scratch(scratch)
