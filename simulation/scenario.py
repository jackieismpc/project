"""试验场景：决定每支试管的真实凝固状态。

合成视频按场景绘制试管内容；脚本化的 :class:`~simulation.mock_analyzer.MockAnalyzer`
按场景直接给出结果。场景由随机种子和管号决定，同一场景每次得到相同的状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from gelinspect.domain.enums import GelState
from gelinspect.domain.plate import HOLES_PER_ROW, tubes_in_row
from simulation.image_synth import TubeSpec, sample_tube_spec

_ORDER = (GelState.FULLY_GELLED, GelState.PARTIALLY_GELLED, GelState.NOT_GELLED)


@dataclass(slots=True)
class Scenario:
    """每支试管的状态来源。

    Attributes:
        seed: 随机种子。
        state_ratios: 完全凝固、部分凝固、未凝固的比例，不要求和为 1。
        tube_states: 按管号显式指定的状态，优先于比例；值为 None 表示该管位没有试管。
    """

    seed: int = 0
    state_ratios: tuple[float, float, float] = (0.5, 0.2, 0.3)
    tube_states: Mapping[int, GelState | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验比例。

        Raises:
            ValueError: 比例为负或全为 0。
        """
        if any(r < 0 for r in self.state_ratios) or sum(self.state_ratios) <= 0:
            raise ValueError(f"比例必须非负且不全为 0：{self.state_ratios}")

    def state_for(self, tube_no: int) -> GelState | None:
        """查询某支试管的状态。

        Args:
            tube_no: 管号。

        Returns:
            显式指定的状态，否则按比例抽取；None 表示没有试管。
        """
        if tube_no in self.tube_states:
            return self.tube_states[tube_no]
        total = sum(self.state_ratios)
        draw = float(np.random.default_rng([self.seed, tube_no, 1]).uniform(0, total))
        running = 0.0
        for state, ratio in zip(_ORDER, self.state_ratios, strict=True):
            running += ratio
            if draw < running:
                return state
        return _ORDER[-1]

    def spec_for(self, tube_no: int) -> TubeSpec | None:
        """某支试管的绘制参数。

        Args:
            tube_no: 管号。

        Returns:
            试管内容，没有试管时为 None。
        """
        state = self.state_for(tube_no)
        if state is None:
            return None
        return sample_tube_spec(state, np.random.default_rng([self.seed, tube_no, 2]))

    def specs_for_row(self, row_index: int) -> list[TubeSpec | None]:
        """一排 8 个管位的绘制参数。

        Args:
            row_index: 排号，1–10。

        Returns:
            长度为 8 的列表。
        """
        specs = [self.spec_for(no) for no in tubes_in_row(row_index)]
        assert len(specs) == HOLES_PER_ROW
        return specs
