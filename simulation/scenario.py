"""试验场景：决定每支试管的真实凝固状态。

合成视频按场景绘制试管内容；脚本化的 :class:`~simulation.mock_analyzer.MockAnalyzer`
按场景直接给出结果。场景由随机种子和管号决定，同一场景每次得到相同的状态。

状态的来源按优先级：显式的 ``tube_states`` → 孔板内容（:class:`PlatePlan`）的剂量关系 → 比例抽取。
剂量关系见 :func:`dose_response_states`：高浓度阳性、低浓度阴性、阴性对照阴性，边界附近有部分凝固。
用途未确认或没有外加内毒素的管（推定模板）没有剂量可依，仍按比例抽取。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from gelinspect.domain.enums import GelState, TubeRole
from gelinspect.domain.plate import HOLES_PER_ROW, tubes_in_row
from gelinspect.interfaces.device import PlatePlan
from simulation.image_synth import TubeSpec, sample_tube_spec

_ORDER = (GelState.FULLY_GELLED, GelState.PARTIALLY_GELLED, GelState.NOT_GELLED)
_ALWAYS_POSITIVE_RATIO = 2.0
"""外加内毒素不低于 2λ 的管总是完全凝固。"""
_ALWAYS_NEGATIVE_RATIO = 0.25
"""外加内毒素不高于 λ/4 的管总是未凝固。"""
_ENDPOINT_AT_LAMBDA = 0.6
"""一个系列的真实终点落在 λ 的概率，其余落在 λ/2。"""
_PARTIAL_AT_ENDPOINT = 0.3
"""终点那一管表现为部分凝固的概率。"""
_PARTIAL_BELOW_ENDPOINT = 0.2
"""比终点低一档的那一管表现为部分凝固的概率。"""


def dose_response_states(plan: PlatePlan, seed: int = 0) -> dict[int, GelState]:
    """按外加内毒素和 λ 生成符合剂量关系的状态。

    同一浓度内按管号排序的第 k 支属于第 k 个系列。每个系列抽一个真实终点（λ 或 λ/2）：高于终点的
    完全凝固，低于终点两档及以下的未凝固，终点和低一档有机会表现为部分凝固，留给人工复核。
    这样同一系列不会出现阴性之后又阳性。

    Args:
        plan: 孔板内容。
        seed: 随机种子。

    Returns:
        有剂量可依的管的状态；用途未确认或没有外加内毒素的管不在其中。λ 未知时为空。
    """
    labeled = plan.labeled_sensitivity
    if labeled is None or labeled <= 0:
        return {}
    states: dict[int, GelState] = {}
    ranks: dict[float, int] = {}
    for tube in sorted(plan.tubes, key=lambda t: t.tube_no):
        if tube.role is TubeRole.NEGATIVE_CONTROL:
            states[tube.tube_no] = GelState.NOT_GELLED
            continue
        if tube.role is TubeRole.UNCONFIRMED or tube.spiked_endotoxin is None:
            continue
        ratio = tube.spiked_endotoxin / labeled
        rank = ranks.get(ratio, 0)
        ranks[ratio] = rank + 1
        endpoint_rng = np.random.default_rng([seed, rank, 7])
        endpoint = 1.0 if float(endpoint_rng.uniform()) < _ENDPOINT_AT_LAMBDA else 0.5
        draw = float(np.random.default_rng([seed, tube.tube_no, 8]).uniform())
        if ratio >= _ALWAYS_POSITIVE_RATIO:
            state = GelState.FULLY_GELLED
        elif ratio <= _ALWAYS_NEGATIVE_RATIO:
            state = GelState.NOT_GELLED
        elif ratio > endpoint:
            state = GelState.FULLY_GELLED
        elif ratio == endpoint:
            state = (
                GelState.PARTIALLY_GELLED if draw < _PARTIAL_AT_ENDPOINT else GelState.FULLY_GELLED
            )
        else:
            state = (
                GelState.PARTIALLY_GELLED if draw < _PARTIAL_BELOW_ENDPOINT else GelState.NOT_GELLED
            )
        states[tube.tube_no] = state
    return states


@dataclass(slots=True)
class Scenario:
    """每支试管的状态来源。

    Attributes:
        seed: 随机种子。
        state_ratios: 完全凝固、部分凝固、未凝固的比例，不要求和为 1。
        tube_states: 按管号显式指定的状态，优先于比例；值为 None 表示该管位没有试管。
        plate_plan: 孔板内容。设置后，不在孔板上的管位没有试管，其余按剂量关系。
    """

    seed: int = 0
    state_ratios: tuple[float, float, float] = (0.5, 0.2, 0.3)
    tube_states: Mapping[int, GelState | None] = field(default_factory=dict)
    plate_plan: PlatePlan | None = None
    _doses: dict[int, GelState] = field(default_factory=dict, init=False, repr=False)
    _on_plate: frozenset[int] = field(default=frozenset(), init=False, repr=False)

    def __post_init__(self) -> None:
        """校验比例。

        Raises:
            ValueError: 比例为负或全为 0。
        """
        if any(r < 0 for r in self.state_ratios) or sum(self.state_ratios) <= 0:
            raise ValueError(f"比例必须非负且不全为 0：{self.state_ratios}")
        if self.plate_plan is not None:
            self._doses = dose_response_states(self.plate_plan, self.seed)
            self._on_plate = frozenset(t.tube_no for t in self.plate_plan.tubes)

    def with_plate_plan(self, plan: PlatePlan) -> Scenario:
        """换一块孔板。

        Args:
            plan: 孔板内容。

        Returns:
            新场景，种子、比例和显式状态不变。
        """
        return Scenario(self.seed, self.state_ratios, self.tube_states, plan)

    def state_for(self, tube_no: int) -> GelState | None:
        """查询某支试管的状态。

        Args:
            tube_no: 管号。

        Returns:
            显式指定的状态，否则按比例抽取；None 表示没有试管。
        """
        if tube_no in self.tube_states:
            return self.tube_states[tube_no]
        if self.plate_plan is not None:
            if tube_no not in self._on_plate:
                return None
            if tube_no in self._doses:
                return self._doses[tube_no]
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
            row_index: 排号，1–12。

        Returns:
            长度为 8 的列表。
        """
        specs = [self.spec_for(no) for no in tubes_in_row(row_index)]
        assert len(specs) == HOLES_PER_ROW
        return specs
