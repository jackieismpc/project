"""示例试验数据：灵敏度复核（2λ:1）。

17 支试管，λ=0.5 时外加内毒素列为 1 / 0.5 / 0.25 / 0.125 各 4 管加 1 支 0（阴性对照）。
状态：1–6 号完全凝固（阳性），7 号部分凝固（待复核），8 号及 9–17 号未凝固（阴性）。
7 号复核为阳性时，4 个系列的终点是 0.5、0.5、0.5、1，λc≈0.59。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from types import MappingProxyType

from gelinspect.domain.entities import Experiment, ReagentBatch, Sample, TubeAssignment
from gelinspect.domain.enums import ExperimentType, GelState, Interpretation, ReagentKind
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.plate import TUBE_COUNT
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from simulation.reagents import WHITELIST
from simulation.scenario import Scenario

SENSITIVITY_STATES: Mapping[int, GelState] = MappingProxyType(
    {
        **dict.fromkeys(range(1, 7), GelState.FULLY_GELLED),
        7: GelState.PARTIALLY_GELLED,
        **dict.fromkeys(range(8, 18), GelState.NOT_GELLED),
    }
)
"""灵敏度复核示例中每支试管的真实状态。"""

SENSITIVITY_EXPECTED: Mapping[int, Interpretation] = MappingProxyType(
    {no: GelStateMapping().for_state(state) for no, state in SENSITIVITY_STATES.items()}
)
"""按默认映射得到的预期判读：1–6 阳性，7 待复核，其余阴性。"""

SAMPLE_LAMBDA = 0.5
"""示例试验的标示灵敏度，取参考视频里的 0.5（模拟白名单的 ``LAL2606A`` 是 0.25）。"""

SENSITIVITY_ENDOTOXIN: Mapping[int, float] = MappingProxyType(
    {
        item.tube_no: item.spike_for(SAMPLE_LAMBDA) or 0.0
        for item in BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION].items
    }
)
"""外加内毒素列：1–4 号 1.0，5–8 号 0.5，9–12 号 0.25，13–16 号 0.125，17 号 0.0。"""


def sensitivity_scenario(seed: int = 0) -> Scenario:
    """灵敏度复核示例的场景：1–17 号按示例状态，其余管位没有试管。

    Args:
        seed: 随机种子，决定每支试管的外观细节。

    Returns:
        场景。
    """
    states: dict[int, GelState | None] = dict.fromkeys(range(1, TUBE_COUNT + 1))
    states.update(SENSITIVITY_STATES)
    return Scenario(seed=seed, tube_states=states)


def sensitivity_reagents() -> tuple[ReagentBatch, ...]:
    """示例试验使用的三种有效试剂批次，来自模拟白名单。

    Returns:
        鲎试剂、内毒素、检测用水各一个批次。
    """
    wanted = {"LAL2606A", "CSE2605A", "WAT2607A"}
    return tuple(
        ReagentBatch(
            kind=entry.kind,
            batch_no=entry.batch_no,
            expiry_date=entry.expiry_date,
            registered=True,
            labeled_sensitivity=(
                SAMPLE_LAMBDA
                if entry.kind is ReagentKind.LAL_REAGENT
                else entry.labeled_sensitivity
            ),
        )
        for entry in WHITELIST
        if entry.batch_no in wanted
    )


def sensitivity_experiment(created_at: datetime | None = None) -> Experiment:
    """构造灵敏度复核示例试验，状态为草稿，模板取自内置模板。

    Args:
        created_at: 创建时间，必须带时区，默认 2026-11-04 09:00 UTC。

    Returns:
        试验聚合根。
    """
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    assignments = tuple(
        TubeAssignment(
            tube_no=item.tube_no,
            sample=Sample(
                sample_id=f"S{item.tube_no:02d}",
                name=item.sample_name,
                serial_no=f"SN{item.tube_no:04d}",
                batch_no="SB2606",
            ),
            role=item.role,
            dilution_factor=item.dilution_factor,
            spiked_endotoxin=item.spike_for(SAMPLE_LAMBDA),
        )
        for item in template.items
    )
    return Experiment(
        experiment_id="EXP-SAMPLE-0001",
        experiment_type=template.experiment_type,
        template_id=template.template_id,
        instrument_no="GI-001",
        operator="operator01",
        created_at=created_at or datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        assignments=assignments,
        reagents=sensitivity_reagents(),
        layout_confirmed=True,
    )


SAMPLE_DATE = date(2026, 11, 4)
"""示例试验的日期，此日期下白名单中的批次除 ``LAL2501B`` 外都在有效期内。"""
