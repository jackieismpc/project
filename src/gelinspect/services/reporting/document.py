"""报告的内容模型：和语言、排版无关的数据，加上页脚的“内容摘要”。

报告由若干 :class:`ExperimentSection` 组成：单个试验的报告只有一节，单批次报告每个试验一节。
内容摘要是这份数据的 SHA-256（规范化 JSON，时间一律用 UTC），**不是文件的哈希**：文件哈希写在
文件里会自己改变自己。所以同一份数据，不论用中文还是英文、在哪个时区渲染，摘要都相同；
文件本身的完整性由 PDF 数字签名和库里记录的文件 SHA-256 保证。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from PySide6.QtGui import QImage

from gelinspect.domain.conclusions import ExperimentConclusion, conclusion_payload
from gelinspect.domain.entities import Experiment
from gelinspect.domain.enums import (
    BatchKind,
    ExperimentType,
    Interpretation,
    ReagentKind,
    ReportScope,
    ReviewStatus,
)
from gelinspect.domain.state_machine import ExperimentState


@dataclass(frozen=True, slots=True)
class ReportRowData:
    """结果表的一行。

    Attributes:
        tube_no: 管号。
        sample_name: 样品名称。
        serial_no: 序列号。
        batch_no: 样品批号。
        dilution_factor: 稀释倍数。
        spiked_endotoxin: 外加内毒素（EU/mL）。
        interpretation: 最终判读（人工复核后以复核为准）；没有结果为 None。
        reviewed: 判读是否来自人工复核。
    """

    tube_no: int
    sample_name: str
    serial_no: str | None
    batch_no: str | None
    dilution_factor: float | None
    spiked_endotoxin: float | None
    interpretation: Interpretation | None
    reviewed: bool


@dataclass(frozen=True, slots=True)
class ExperimentSection:
    """一个试验在报告里的内容。

    Attributes:
        experiment_id: 试验标识，作为报告的文件名。
        experiment_type: 试验类型。
        state: 试验状态。
        instrument_no: 仪器编号。
        started_at: 开始检测的时间。
        temperature_range: 温度日志里真实的最低和最高温度（℃），没有日志为 None。
        operator_name: 操作人显示名。
        signer_name: 签署人显示名。
        signed_at: 签署时间。
        labeled_sensitivity: 标示灵敏度 λ（EU/mL）。
        lal_batch: 鲎试剂批号。
        endotoxin_batch: 内毒素批号。
        water_batch: 检测用水批号。
        mvd: 最大有效稀释倍数（录入项，未录入为 None）。
        endotoxin_limit: 内毒素限值（录入项，未录入为 None）。
        rows: 结果表，按管号升序。
        conclusion: 入库的结论；没有（旧试验）为 None；结论功能关闭时恒为 None。
        conclusions_enabled: 结论功能是否打开。关闭时报告不含结论区、λc、规则版本，
            内容摘要也不含结论。
    """

    experiment_id: str
    experiment_type: ExperimentType
    state: ExperimentState
    instrument_no: str
    started_at: datetime | None
    temperature_range: tuple[float, float] | None
    operator_name: str
    signer_name: str | None
    signed_at: datetime | None
    labeled_sensitivity: float | None
    lal_batch: str | None
    endotoxin_batch: str | None
    water_batch: str | None
    mvd: float | None
    endotoxin_limit: float | None
    rows: tuple[ReportRowData, ...]
    conclusion: ExperimentConclusion | None
    conclusions_enabled: bool = False


@dataclass(frozen=True, slots=True)
class RowImagePair:
    """一排的翻转前后代表帧缩略图（8 连图），只用于排版，不进内容摘要。

    Attributes:
        row_index: 排号。
        before: 翻转前。
        after: 翻转后。
    """

    row_index: int
    before: QImage | None
    after: QImage | None


@dataclass(frozen=True, slots=True)
class ReportDocument:
    """一份报告。

    Attributes:
        scope: 单个试验或单批次。
        sections: 各试验的内容。
        copyright_holder: 版权声明的权利人。
        signed_by: 签署人显示名；预览（还没有签署）为 None。
        signed_at: 签署时间。
        batch_kind: 单批次报告按哪种批号汇总。
        batch_no: 批号。
        images: 试验标识到 8 连图缩略页的映射，没有附图时为空。
    """

    scope: ReportScope
    sections: tuple[ExperimentSection, ...]
    copyright_holder: str
    signed_by: str | None = None
    signed_at: datetime | None = None
    batch_kind: BatchKind | None = None
    batch_no: str | None = None
    images: Mapping[str, tuple[RowImagePair, ...]] = field(default_factory=dict)

    @property
    def signed(self) -> bool:
        """是否已经签署（预览为 False）。"""
        return self.signed_by is not None

    def content_digest(self) -> str:
        """内容摘要：报告数据的 SHA-256，十六进制小写。

        Returns:
            64 位十六进制文本。
        """
        payload = {
            "scope": self.scope.value,
            "batch_kind": None if self.batch_kind is None else self.batch_kind.value,
            "batch_no": self.batch_no,
            "signed_by": self.signed_by,
            "signed_at": _utc(self.signed_at),
            "sections": [_section_payload(section) for section in self.sections],
        }
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _section_payload(section: ExperimentSection) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "experiment_id": section.experiment_id,
        "experiment_type": section.experiment_type.value,
        "state": section.state.value,
        "instrument_no": section.instrument_no,
        "started_at": _utc(section.started_at),
        "temperature_range": section.temperature_range,
        "operator": section.operator_name,
        "signer": section.signer_name,
        "signed_at": _utc(section.signed_at),
        "labeled_sensitivity": section.labeled_sensitivity,
        "batches": [section.lal_batch, section.endotoxin_batch, section.water_batch],
        "mvd": section.mvd,
        "endotoxin_limit": section.endotoxin_limit,
        "rows": [
            [
                row.tube_no,
                row.sample_name,
                row.serial_no,
                row.batch_no,
                row.dilution_factor,
                row.spiked_endotoxin,
                None if row.interpretation is None else row.interpretation.value,
                row.reviewed,
            ]
            for row in section.rows
        ],
    }
    if section.conclusions_enabled:
        payload["conclusion"] = (
            None
            if section.conclusion is None
            else {
                "verdict": section.conclusion.verdict.value,
                "value": section.conclusion.value,
                "rule_version": section.conclusion.rule_version,
                "payload": conclusion_payload(section.conclusion),
            }
        )
    return payload


def build_section(
    experiment: Experiment,
    *,
    operator_name: str,
    signer_name: str | None,
    started_at: datetime | None,
    temperature_range: tuple[float, float] | None,
    conclusions_enabled: bool = False,
) -> ExperimentSection:
    """由试验聚合生成报告里的一节。

    Args:
        experiment: 试验（已签署时带签署人和时间）。
        operator_name: 操作人显示名。
        signer_name: 签署人显示名。
        started_at: 开始检测的时间。
        temperature_range: 温度日志的最低和最高温度。
        conclusions_enabled: 结论功能是否打开；关闭时这一节不带结论。

    Returns:
        报告内容的一节。
    """
    batches = {reagent.kind: reagent for reagent in experiment.reagents}
    lal = batches.get(ReagentKind.LAL_REAGENT)
    labeled = lal.labeled_sensitivity if lal is not None else None
    if labeled is None and experiment.conclusion is not None:
        labeled = experiment.conclusion.labeled_sensitivity
    rows: list[ReportRowData] = []
    for assignment in sorted(experiment.assignments, key=lambda a: a.tube_no):
        result = experiment.results.get(assignment.tube_no)
        rows.append(
            ReportRowData(
                tube_no=assignment.tube_no,
                sample_name=assignment.sample.name,
                serial_no=assignment.sample.serial_no,
                batch_no=assignment.sample.batch_no,
                dilution_factor=assignment.dilution_factor,
                spiked_endotoxin=assignment.spiked_endotoxin,
                interpretation=None if result is None else result.final_interpretation,
                reviewed=result is not None and result.review_status is ReviewStatus.REVIEWED,
            )
        )
    endotoxin = batches.get(ReagentKind.ENDOTOXIN)
    water = batches.get(ReagentKind.WATER)
    return ExperimentSection(
        experiment_id=experiment.experiment_id,
        experiment_type=experiment.experiment_type,
        state=experiment.state,
        instrument_no=experiment.instrument_no,
        started_at=started_at or experiment.created_at,
        temperature_range=temperature_range,
        operator_name=operator_name,
        signer_name=signer_name,
        signed_at=experiment.signed_at,
        labeled_sensitivity=labeled,
        lal_batch=None if lal is None else lal.batch_no,
        endotoxin_batch=None if endotoxin is None else endotoxin.batch_no,
        water_batch=None if water is None else water.batch_no,
        mvd=experiment.mvd,
        endotoxin_limit=experiment.endotoxin_limit,
        rows=tuple(rows),
        conclusion=experiment.conclusion if conclusions_enabled else None,
        conclusions_enabled=conclusions_enabled,
    )
