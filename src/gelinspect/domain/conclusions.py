"""已存结论的历史数据结构。

试验级规则已退役；本模块仅供数据库保留旧记录时读取与往返保存，不提供结论计算入口。
新完成的试验不生成或展示这些数据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    Interpretation,
    TubeRole,
)


@dataclass(frozen=True, slots=True)
class ConclusionNote:
    """旧结论中保存的一条依据文本。"""

    code: str
    params: Mapping[str, str]
    text: str = ""


@dataclass(frozen=True, slots=True)
class TubeEvidence:
    """旧结论记录的单管证据。"""

    tube_no: int
    role: TubeRole
    spiked_endotoxin: float | None
    interpretation: Interpretation | None
    reviewed: bool = False
    series: int | None = None


@dataclass(frozen=True, slots=True)
class ExperimentConclusion:
    """旧版试验级结论记录，仅用于持久化历史数据。"""

    experiment_type: ExperimentType
    verdict: ConclusionVerdict
    value: float | None
    basis: tuple[ConclusionNote, ...]
    unmet: tuple[ConclusionNote, ...]
    rule_version: str
    rule_source: str
    rule_verdict_enabled: bool
    labeled_sensitivity: float | None
    tubes: tuple[TubeEvidence, ...]
    computed_at: datetime | None = None


def conclusion_payload(conclusion: ExperimentConclusion) -> dict[str, Any]:
    """把历史结论的依据和单管证据转为 JSON 值。"""

    def notes(items: tuple[ConclusionNote, ...]) -> list[dict[str, Any]]:
        return [
            {"code": note.code, "params": dict(note.params), "text": note.text} for note in items
        ]

    return {
        "basis": notes(conclusion.basis),
        "unmet": notes(conclusion.unmet),
        "rule_source": conclusion.rule_source,
        "rule_verdict_enabled": conclusion.rule_verdict_enabled,
        "tubes": [
            {
                "tube_no": tube.tube_no,
                "role": tube.role.value,
                "spiked_endotoxin": tube.spiked_endotoxin,
                "interpretation": (
                    None if tube.interpretation is None else tube.interpretation.value
                ),
                "reviewed": tube.reviewed,
                "series": tube.series,
            }
            for tube in conclusion.tubes
        ],
    }


def conclusion_from_payload(
    *,
    experiment_type: ExperimentType,
    verdict: ConclusionVerdict,
    value: float | None,
    rule_version: str,
    labeled_sensitivity: float | None,
    computed_at: datetime | None,
    payload: Mapping[str, Any],
) -> ExperimentConclusion:
    """从历史 JSON 还原原值，不重新解释或计算。"""

    def notes(key: str) -> tuple[ConclusionNote, ...]:
        return tuple(
            ConclusionNote(
                code=str(item["code"]),
                params={str(name): str(value) for name, value in item["params"].items()},
                text=str(item.get("text", "")),
            )
            for item in payload[key]
        )

    return ExperimentConclusion(
        experiment_type=experiment_type,
        verdict=verdict,
        value=value,
        basis=notes("basis"),
        unmet=notes("unmet"),
        rule_version=rule_version,
        rule_source=str(payload["rule_source"]),
        rule_verdict_enabled=bool(payload["rule_verdict_enabled"]),
        labeled_sensitivity=labeled_sensitivity,
        tubes=tuple(
            TubeEvidence(
                tube_no=int(tube["tube_no"]),
                role=TubeRole(tube["role"]),
                spiked_endotoxin=tube["spiked_endotoxin"],
                interpretation=(
                    None
                    if tube["interpretation"] is None
                    else Interpretation(tube["interpretation"])
                ),
                reviewed=bool(tube["reviewed"]),
                series=tube["series"],
            )
            for tube in payload["tubes"]
        ),
        computed_at=computed_at,
    )
