"""相机看到的场景：当前排、翻转进度和样品流动进度。

:class:`~simulation.sim_device.SimDevice` 提供场景，也可以用 :class:`StaticScene` 手动设置。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SceneSnapshot:
    """某一时刻相机看到的场景。

    Attributes:
        row_index: 相机对着的排号，1–10。
        flip_phase: 翻转进度，0 为翻转前，1 为翻转后，中间为翻转过程。
        settle: 样品团的流动进度，0 为刚翻转，1 为已稳定。
        at: 场景被采样的时刻。相机的「曝光时刻」，帧的 ``captured_at`` 必须用它：渲染要花时间，
            渲染完再取时间，高负载下一帧翻转中的画面会被标成翻转完成之后。没有动作的静态场景
            为 None。
    """

    row_index: int = 1
    flip_phase: float = 0.0
    settle: float = 0.0
    at: datetime | None = None


class SceneProvider(Protocol):
    """场景来源。"""

    def scene_snapshot(self) -> SceneSnapshot:
        """读取当前场景。

        Returns:
            场景快照。
        """
        ...


class StaticScene:
    """手动设置的场景，不依赖设备。"""

    def __init__(self, snapshot: SceneSnapshot | None = None) -> None:
        """创建场景。

        Args:
            snapshot: 初始场景，默认第 1 排、翻转前。
        """
        self._snapshot = snapshot or SceneSnapshot()

    def set(self, row_index: int, *, flipped: bool = False, settled: bool | None = None) -> None:
        """设置场景为稳定的翻转前或翻转后。

        Args:
            row_index: 排号，1–10。
            flipped: 是否已翻转。
            settled: 样品团是否已稳定，默认与 ``flipped`` 一致。
        """
        settle = flipped if settled is None else settled
        self._snapshot = SceneSnapshot(row_index, 1.0 if flipped else 0.0, 1.0 if settle else 0.0)

    def set_progress(self, row_index: int, flip_phase: float, settle: float = 0.0) -> None:
        """设置任意进度，用于定位翻转过程中的画面。

        Args:
            row_index: 排号，1–10。
            flip_phase: 翻转进度，0–1。
            settle: 流动进度，0–1。
        """
        self._snapshot = SceneSnapshot(row_index, flip_phase, settle)

    def scene_snapshot(self) -> SceneSnapshot:
        """读取当前场景。

        Returns:
            场景快照。
        """
        return self._snapshot
