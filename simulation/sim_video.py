"""模拟视频源：``VideoSource`` 的实现，实时生成整排试管的画面。

画面内容由两部分决定：

- :class:`~simulation.scene.SceneProvider`：当前排、翻转进度、样品流动进度。默认跟随本进程最近创建
  的 :class:`~simulation.sim_device.SimDevice`；没有设备时用
  :class:`~simulation.scene.StaticScene`，可以通过 :attr:`SimVideoSource.scene` 手动设置。
- :class:`~simulation.scenario.Scenario`：每支试管的真实状态。

翻转过程中会输出过渡帧（试管绕管中心翻转，样品团随后流动）。帧按 ``fps`` 的节奏产出，
等待通过可注入的时钟完成。可以注入断流和读超时。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace

import numpy as np

from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
)
from simulation.clock import Clock, SystemClock
from simulation.image_synth import SynthConfig, Uint8Array, add_noise, render_row
from simulation.scenario import Scenario
from simulation.scene import SceneProvider, SceneSnapshot, StaticScene
from simulation.sim_device import SimDevice

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 15.0
_CACHE_SIZE = 24


class SimVideoSource:
    """模拟视频源，可以无参构造。

    Attributes:
        scene: 场景来源，None 时跟随最近创建的 ``SimDevice``，没有设备则使用静态场景。
        scenario: 每支试管的真实状态。
    """

    def __init__(
        self,
        *,
        scene: SceneProvider | None = None,
        scenario: Scenario | None = None,
        synth: SynthConfig | None = None,
        clock: Clock | None = None,
        seed: int = 0,
    ) -> None:
        """创建视频源，不做耗时操作。

        Args:
            scene: 场景来源。
            scenario: 试管状态场景，默认 ``Scenario()``。
            synth: 画面参数，分辨率会被 ``open`` 中的配置覆盖。
            clock: 时钟，默认系统时钟。
            seed: 噪声的随机种子。
        """
        self.scene = scene
        self.scenario = scenario or Scenario()
        self._synth = synth or SynthConfig(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT)
        self._clock: Clock = clock or SystemClock()
        self._seed = seed
        self._static = StaticScene()
        self._opened = False
        self._config = VideoConfig(device="sim")
        self._interval = 1.0 / DEFAULT_FPS
        self._next_due = 0.0
        self._sequence = 0
        self._disconnected = False
        self._timeouts = 0
        self._cache: OrderedDict[tuple[int, int, int], Uint8Array] = OrderedDict()

    @property
    def static_scene(self) -> StaticScene:
        """没有设备时使用的静态场景，可手动设置。"""
        return self._static

    def inject_disconnect(self) -> None:
        """模拟断流：下一次 ``read`` 抛 ``VideoDisconnectedError``，需要重新 ``open`` 恢复。"""
        self._disconnected = True

    def inject_timeouts(self, count: int) -> None:
        """让接下来的 ``count`` 次 ``read`` 返回 None（读超时）。

        Args:
            count: 超时次数。
        """
        self._timeouts = count

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开视频源，同时清除断流注入。

        Args:
            config: 视频源配置；``width``、``height``、``fps`` 为空时用 1920×1080、15 fps。

        Returns:
            实际参数，总是实时源。
        """
        width = config.width or DEFAULT_WIDTH
        height = config.height or DEFAULT_HEIGHT
        fps = config.fps or DEFAULT_FPS
        self._config = config
        self._synth = replace(self._synth, width=width, height=height)
        self._interval = 1.0 / fps
        self._next_due = self._clock.monotonic()
        self._sequence = 0
        self._disconnected = False
        self._cache.clear()
        self._opened = True
        return VideoInfo(
            width=width, height=height, fps=fps, is_live=True, description="synthetic backlit row"
        )

    def read(self) -> VideoFrame | None:
        """读取下一帧，按帧率等待。

        Returns:
            下一帧；注入读超时，或下一帧在 ``read_timeout_ms`` 内没有到点时返回 None。

        Raises:
            VideoNotOpenError: 未打开或已关闭。
            VideoDisconnectedError: 注入了断流。
        """
        if not self._opened:
            raise VideoNotOpenError("视频源未打开")
        if self._disconnected:
            raise VideoDisconnectedError("模拟的视频断流")
        timeout = self._config.read_timeout_ms / 1000
        if self._timeouts > 0:
            self._timeouts -= 1
            self._clock.sleep(timeout)
            return None
        wait = self._next_due - self._clock.monotonic()
        if wait > timeout:
            self._clock.sleep(timeout)
            return None
        self._clock.sleep(wait)
        now = self._clock.monotonic()
        self._next_due = max(self._next_due + self._interval, now - 1.0)
        snapshot = self._provider().scene_snapshot()
        captured_at = snapshot.at or self._clock.now()
        frame = VideoFrame(
            image=self._render(snapshot),
            sequence=self._sequence,
            captured_at=captured_at,
            monotonic_s=now,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        """关闭视频源，可重复调用。"""
        self._opened = False
        self._cache.clear()

    def _provider(self) -> SceneProvider:
        if self.scene is not None:
            return self.scene
        device = SimDevice.latest()
        return device if device is not None else self._static

    def _render(self, snapshot: SceneSnapshot) -> Uint8Array:
        """渲染场景。无噪声的底图按进度量化后缓存，每帧只重新加噪声。"""
        key = (snapshot.row_index, round(snapshot.flip_phase * 60), round(snapshot.settle * 30))
        base = self._cache.get(key)
        if base is None:
            specs = self.scenario.specs_for_row(snapshot.row_index)
            clean = replace(self._synth, noise_sigma=0.0)
            base = render_row(
                specs,
                clean,
                flip_phase=key[1] / 60,
                settle=key[2] / 30,
                truth=False,
            ).image
            self._cache[key] = base
            if len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        image = base.copy()
        add_noise(
            image, self._synth.noise_sigma, np.random.default_rng([self._seed, self._sequence])
        )
        return image
