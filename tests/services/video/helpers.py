"""视频管线测试的公共工具：带已知时间戳和编号的假视频源、等待函数、配置构造。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)
from gelinspect.services.video.config import (
    PreviewConfig,
    QualityConfig,
    ReconnectConfig,
    SnapshotConfig,
    VideoPipelineConfig,
)

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)


def wait_until(
    predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.005
) -> bool:
    """轮询直到条件成立或超时。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def frame_number(image: np.ndarray[Any, Any]) -> int:
    """读出 :class:`FakeSource` 写在图像左上角的帧号。"""
    pixel = image[0, 0]
    return int(pixel[0]) + 256 * int(pixel[1]) + 65536 * int(pixel[2])


def stamped_image(base: np.ndarray[Any, Any], number: int) -> np.ndarray[Any, Any]:
    """复制底图并把帧号编码进左上角像素。"""
    image = base.copy()
    image[0, 0] = (number % 256, (number // 256) % 256, (number // 65536) % 256)
    return image


class FakeSource:
    """带已知时间戳和编号的假视频源。

    第 k 帧的 ``sequence`` 为 k，``captured_at`` 为 ``T0 + k * frame_dt``
    （合成时间，与真实时间无关），图像左上角像素编码帧号。真实产帧间隔为
    ``interval_s``，因此测试可以用很短的真实时间跑出很长的「视频时间」。

    ``read`` 记录调用线程，用来断言所有方法都在同一个采集线程上执行。
    """

    def __init__(
        self,
        *,
        width: int = 64,
        height: int = 48,
        frame_dt: float = 0.1,
        interval_s: float = 0.002,
        fps: float | None = 10.0,
        max_frames: int | None = None,
        noise: bool = True,
        base_value: int = 128,
        disconnect_after: int | None = None,
        open_failures: int = 0,
    ) -> None:
        self.width, self.height = width, height
        self.frame_dt = frame_dt
        self.interval_s = interval_s
        self.fps = fps
        self.max_frames = max_frames
        self.disconnect_after = disconnect_after
        self.open_failures = open_failures
        self._noise = noise
        self._rng = np.random.default_rng(0)
        self._base = np.full((height, width, 3), base_value, np.uint8)
        self._lock = threading.Lock()
        self.opened = False
        self.open_calls = 0
        self.close_calls = 0
        self.next_sequence = 0
        self.produced = 0
        self.threads: set[int] = set()
        self.paused = threading.Event()
        self.read_delay_s = 0.0

    def video_time(self, sequence: int) -> datetime:
        return T0 + timedelta(seconds=sequence * self.frame_dt)

    def open(self, config: VideoConfig) -> VideoInfo:
        self.threads.add(threading.get_ident())
        self.open_calls += 1
        if self.open_failures > 0:
            self.open_failures -= 1
            raise VideoOpenError("假相机：打开失败")
        self.opened = True
        return VideoInfo(
            width=self.width, height=self.height, fps=self.fps, is_live=True, description="fake"
        )

    def read(self) -> VideoFrame | None:
        self.threads.add(threading.get_ident())
        if not self.opened:
            raise VideoNotOpenError("未打开")
        if self.paused.is_set() or (
            self.max_frames is not None and self.produced >= self.max_frames
        ):
            time.sleep(0.01)
            return None
        if self.disconnect_after is not None and self.produced >= self.disconnect_after:
            self.disconnect_after = None
            self.opened = False
            raise VideoDisconnectedError("假相机：断开")
        time.sleep(self.interval_s + self.read_delay_s)
        sequence = self.next_sequence
        self.next_sequence += 1
        self.produced += 1
        image = stamped_image(self._base, sequence)
        if self._noise:
            image[1:, :, :] = self._rng.integers(100, 160, image[1:].shape, dtype=np.uint8)
            image[0, 0] = (sequence % 256, (sequence // 256) % 256, (sequence // 65536) % 256)
        return VideoFrame(
            image=image,
            sequence=sequence,
            captured_at=self.video_time(sequence),
            monotonic_s=time.monotonic(),
        )

    def close(self) -> None:
        self.threads.add(threading.get_ident())
        self.close_calls += 1
        self.opened = False


def make_config(
    data_dir: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    reconnect: ReconnectConfig | None = None,
    quality: QualityConfig | None = None,
    snapshot: SnapshotConfig | None = None,
    preview: PreviewConfig | None = None,
    **overrides: Any,
) -> VideoPipelineConfig:
    """构造测试用配置：重连等待很短，抓帧超时较短。"""
    return VideoPipelineConfig(
        data_dir=data_dir,
        source=VideoConfig(device="fake", width=width, height=height),
        reconnect=reconnect
        or ReconnectConfig(initial_delay_s=0.02, max_delay_s=0.05, stall_disconnect_s=None),
        quality=quality or QualityConfig(enabled=True, watchdog_timeout_s=5.0),
        snapshot=snapshot or SnapshotConfig(wait_timeout_s=3.0, settle_s=1.0),
        preview=preview or PreviewConfig(),
        stop_timeout_s=5.0,
        **overrides,
    )
