"""Excel（.xlsx）导出：把试验结果、历史列表和审计写成工作簿。

只负责格式和安全，不碰数据库和权限（权限在 ``export_service``）。要点：

* **公式注入防护**：以 ``=``、``+``、``-``、``@`` 或制表符、回车开头的文本，在前面补一个半角单引号，
  Excel 就把它当普通文字显示，不会当公式执行（样品名称、复核备注等都是用户输入的）。
* **文件名安全**：:func:`safe_filename` 去掉路径分隔符、控制字符和 Windows 保留名。
* **中文和英文**：列标题和枚举值的文字经 :class:`ExportLabels` 翻译；工作簿用 UTF-8（xlsx 本身
  就是 Unicode），中文不会乱码。
* 大数据量用 openpyxl 的只写模式，一万管约一两秒。
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from gelinspect.domain.conclusions import ConclusionNote
from gelinspect.domain.entities import Experiment, TubeResult
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentKind,
    ReviewStatus,
    Role,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.plate import row_label, tube_to_position
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditRecord, ExperimentSummary

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
"""会被 Excel 当作公式或命令开头的字符。"""

MAX_CELL_LENGTH = 32000
"""单元格文本长度上限（Excel 限制 32767）。"""

_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def sanitize_cell(value: object) -> object:
    """让写入单元格的值不会被 Excel 当公式执行。

    Args:
        value: 要写入的值。数字、布尔和空值原样返回。

    Returns:
        文本值：去掉 Excel 不允许的控制字符，以公式开头字符起头的前面补 ``'``，过长的截断。
    """
    if not isinstance(value, str):
        return value
    text = ILLEGAL_CHARACTERS_RE.sub("", value)
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text
    return text[:MAX_CELL_LENGTH]


def safe_filename(name: str, *, fallback: str = "export", suffix: str = ".xlsx") -> str:
    """把任意文字规整成各平台都能用的文件名。

    Args:
        name: 想用的名称（可以不带扩展名）。
        fallback: 规整后为空时使用的名称。
        suffix: 扩展名，缺少时补上。

    Returns:
        不含路径分隔符、控制字符和保留名的文件名，不超过 100 个字符。
    """
    base = name.strip()
    if base.lower().endswith(suffix.lower()):
        base = base[: -len(suffix)]
    base = _UNSAFE_NAME_CHARS.sub("_", base).strip(" .")
    base = re.sub(r"\.{2,}", ".", re.sub(r"_+", "_", base))[:90].strip(" ._") or fallback
    if base.split(".")[0].upper() in _RESERVED_NAMES:
        base = f"_{base}"
    return base + suffix


def safe_sheet_title(title: str) -> str:
    """规整工作表名：Excel 不允许 [ ] : * ? / 和反斜杠，最长 31 个字符。

    Args:
        title: 想用的名称（翻译后的文字也可能含这些字符）。

    Returns:
        可用的工作表名，规整后为空时是 ``Sheet``。
    """
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", title).strip().strip("'")
    return cleaned[:31] or "Sheet"


def _identity(text: str) -> str:
    return text


def _enum_value(value: Enum) -> str:
    return str(value.value)


@dataclass(frozen=True, slots=True)
class ExportLabels:
    """导出文字的翻译函数。默认原样输出简体中文源文本和枚举值。

    Attributes:
        text: 源文本（简体中文）到当前语言。
        state: 试验状态。
        experiment_type: 试验类型。
        role: 试管用途。
        verdict: 结论。
        interpretation: 阴阳性判读。
        gel_state: 算法三态。
        review_status: 复核状态。
        analysis_status: 算法处理状态。
        user_role: 用户角色（审计的「权限」列）。
        note: 结论依据里的一条。
        reagent: 试剂种类。
    """

    text: Callable[[str], str] = _identity
    state: Callable[[ExperimentState], str] = _enum_value
    experiment_type: Callable[[ExperimentType], str] = _enum_value
    role: Callable[[TubeRole], str] = _enum_value
    verdict: Callable[[ConclusionVerdict], str] = _enum_value
    interpretation: Callable[[Interpretation], str] = _enum_value
    gel_state: Callable[[GelState], str] = _enum_value
    review_status: Callable[[ReviewStatus], str] = _enum_value
    analysis_status: Callable[[TubeAnalysisStatus], str] = _enum_value
    user_role: Callable[[Role], str] = _enum_value
    note: Callable[[ConclusionNote], str] = lambda n: n.text  # noqa: E731
    reagent: Callable[[ReagentKind], str] = _enum_value


EXPORT_TEXTS: tuple[str, ...] = (
    "试验",
    "单管结果",
    "结论依据",
    "历史列表",
    "审计追踪",
    "试验编号",
    "类型",
    "状态",
    "仪器编号",
    "操作人",
    "创建时间",
    "完成时间",
    "签署人",
    "签署时间",
    "鲎试剂批号",
    "内毒素批号",
    "检测用水批号",
    "标示灵敏度 λ（EU/mL）",
    "MVD",
    "内毒素限值",
    "实测灵敏度 λc（EU/mL）",
    "结论",
    "结论数值",
    "规则版本",
    "管数",
    "待复核管数",
    "管号",
    "排",
    "样品名称",
    "序列号",
    "样品批号",
    "稀释倍数",
    "外加内毒素（EU/mL）",
    "用途",
    "分析状态",
    "算法三态",
    "自动判读",
    "复核状态",
    "最终判读",
    "复核人",
    "复核时间",
    "复核备注",
    "评分",
    "置信度",
    "面积变化率 ΔA（%）",
    "轴向偏移 Δs",
    "面积时序标准差 σ_A",
    "轴向时序标准差 σ_s",
    "翻转前面积",
    "翻转后面积",
    "模型版本",
    "配置版本",
    "分析耗时（ms）",
    "算法说明",
    "类别",
    "内容",
    "依据",
    "未满足的前提",
    "序号",
    "时间",
    "操作者",
    "权限",
    "事件类型",
    "操作记录",
    "关联试验",
    "哈希",
)
"""导出里用到的全部源文本（列标题和类别名）。界面把它们登记为可翻译，测试保证两边一致。"""


def _stamp(value: datetime | None) -> str:
    return "" if value is None else value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _feature(result: TubeResult, key: str) -> float | None:
    value = result.features.get(key)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


class _SheetWriter:
    """只写模式的工作表：表头加粗、冻结首行、统一列宽。"""

    def __init__(self, workbook: Workbook, title: str, headers: Sequence[str]) -> None:
        self._workbook = workbook
        self._sheet = workbook.create_sheet(title=safe_sheet_title(title))
        self._sheet.freeze_panes = "A2"
        for index, header in enumerate(headers, start=1):
            self._sheet.column_dimensions[get_column_letter(index)].width = min(
                max(len(header) * 1.6 + 4, 10), 40
            )
        head_cells = []
        for header in headers:
            cell = WriteOnlyCell(self._sheet, value=sanitize_cell(header))
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8EEF0")
            cell.alignment = Alignment(vertical="center")
            head_cells.append(cell)
        self._sheet.append(head_cells)
        self.rows = 0

    def add(self, row: Iterable[object]) -> None:
        self._sheet.append([sanitize_cell(v) for v in row])
        self.rows += 1


def _prepare(destination: Path) -> Path:
    """确定目标文件（缺扩展名时补 ``.xlsx``）并先建好目录：写不进去的目录在建工作簿之前就失败。"""
    target = (
        destination
        if destination.suffix.lower() == ".xlsx"
        else destination.with_suffix(destination.suffix + ".xlsx")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _write_atomically(workbook: Workbook, target: Path) -> Path:
    """先写同目录的临时文件再改名，写到一半失败不会留下损坏的 xlsx。"""
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".export-", suffix=".tmp")
    os.close(handle)
    try:
        workbook.save(temp_name)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def _new_workbook() -> Workbook:
    workbook = Workbook(write_only=True)
    return workbook


def write_results(
    experiments: Iterable[Experiment],
    destination: Path,
    labels: ExportLabels | None = None,
) -> tuple[Path, int]:
    """导出试验结果：试验汇总、单管结果（含特征和复核）、结论依据三张表。

    Args:
        experiments: 试验。
        destination: 目标文件，扩展名不是 ``.xlsx`` 时补上。
        labels: 文字翻译，默认简体中文。

    Returns:
        ``(写出的文件, 单管结果行数)``。
    """
    t = (labels or ExportLabels()).text
    lab = labels or ExportLabels()
    target = _prepare(destination)
    workbook = _new_workbook()
    summary = _SheetWriter(
        workbook,
        t("试验"),
        [
            t(h)
            for h in (
                "试验编号",
                "类型",
                "状态",
                "仪器编号",
                "操作人",
                "创建时间",
                "完成时间",
                "签署人",
                "签署时间",
                "鲎试剂批号",
                "内毒素批号",
                "检测用水批号",
                "标示灵敏度 λ（EU/mL）",
                "MVD",
                "内毒素限值",
                "实测灵敏度 λc（EU/mL）",
                "结论",
                "结论数值",
                "规则版本",
                "管数",
                "待复核管数",
            )
        ],
    )
    tubes = _SheetWriter(
        workbook,
        t("单管结果"),
        [
            t(h)
            for h in (
                "试验编号",
                "管号",
                "排",
                "样品名称",
                "序列号",
                "样品批号",
                "稀释倍数",
                "外加内毒素（EU/mL）",
                "用途",
                "分析状态",
                "算法三态",
                "自动判读",
                "复核状态",
                "最终判读",
                "复核人",
                "复核时间",
                "复核备注",
                "评分",
                "置信度",
                "面积变化率 ΔA（%）",
                "轴向偏移 Δs",
                "面积时序标准差 σ_A",
                "轴向时序标准差 σ_s",
                "翻转前面积",
                "翻转后面积",
                "模型版本",
                "配置版本",
                "分析耗时（ms）",
                "算法说明",
            )
        ],
    )
    basis = _SheetWriter(
        workbook,
        t("结论依据"),
        [t(h) for h in ("试验编号", "类别", "内容")],
    )
    for experiment in experiments:
        batches = {b.kind: b for b in experiment.reagents}
        conclusion = experiment.conclusion
        lal = batches.get(ReagentKind.LAL_REAGENT)
        labeled = lal.labeled_sensitivity if lal is not None else None
        if labeled is None and conclusion is not None:
            labeled = conclusion.labeled_sensitivity
        summary.add(
            [
                experiment.experiment_id,
                lab.experiment_type(experiment.experiment_type),
                lab.state(experiment.state),
                experiment.instrument_no,
                experiment.operator,
                _stamp(experiment.created_at),
                _stamp(experiment.completed_at),
                experiment.signed_by or "",
                _stamp(experiment.signed_at),
                *(
                    batches[k].batch_no if k in batches else ""
                    for k in (ReagentKind.LAL_REAGENT, ReagentKind.ENDOTOXIN, ReagentKind.WATER)
                ),
                labeled,
                experiment.mvd,
                experiment.endotoxin_limit,
                experiment.measured_sensitivity,
                "" if conclusion is None else lab.verdict(conclusion.verdict),
                None if conclusion is None else conclusion.value,
                "" if conclusion is None else conclusion.rule_version,
                len(experiment.assignments),
                len(experiment.pending_review_tubes),
            ]
        )
        for assignment in sorted(experiment.assignments, key=lambda a: a.tube_no):
            result = experiment.results.get(assignment.tube_no)
            row_no = tube_to_position(assignment.tube_no).row_index
            common = [
                experiment.experiment_id,
                assignment.tube_no,
                row_label(row_no),
                assignment.sample.name,
                assignment.sample.serial_no or "",
                assignment.sample.batch_no or "",
                assignment.dilution_factor,
                assignment.spiked_endotoxin,
                lab.role(assignment.role),
            ]
            if result is None:
                tubes.add([*common, *([""] * 20)])
                continue
            tubes.add(
                [
                    *common,
                    lab.analysis_status(result.analysis_status),
                    "" if result.gel_state is None else lab.gel_state(result.gel_state),
                    lab.interpretation(result.auto_interpretation),
                    lab.review_status(result.review_status),
                    lab.interpretation(result.final_interpretation),
                    result.reviewed_by or "",
                    _stamp(result.reviewed_at),
                    result.review_note or "",
                    result.score,
                    result.confidence,
                    _feature(result, "delta_area_pct"),
                    _feature(result, "delta_s"),
                    _feature(result, "sigma_area"),
                    _feature(result, "sigma_axial"),
                    _feature(result, "area_before"),
                    _feature(result, "area_after"),
                    result.model_version or "",
                    result.config_version or "",
                    result.elapsed_ms,
                    result.message or "",
                ]
            )
        if conclusion is not None:
            for note in conclusion.basis:
                basis.add([experiment.experiment_id, t("依据"), lab.note(note)])
            for note in conclusion.unmet:
                basis.add([experiment.experiment_id, t("未满足的前提"), lab.note(note)])
    return _write_atomically(workbook, target), tubes.rows


def write_history(
    items: Iterable[ExperimentSummary],
    destination: Path,
    labels: ExportLabels | None = None,
) -> tuple[Path, int]:
    """导出历史列表（试验摘要）。

    Args:
        items: 试验摘要。
        destination: 目标文件。
        labels: 文字翻译。

    Returns:
        ``(写出的文件, 行数)``。
    """
    lab = labels or ExportLabels()
    t = lab.text
    target = _prepare(destination)
    workbook = _new_workbook()
    sheet = _SheetWriter(
        workbook,
        t("历史列表"),
        [
            t(h)
            for h in (
                "试验编号",
                "类型",
                "状态",
                "仪器编号",
                "操作人",
                "创建时间",
                "完成时间",
                "管数",
                "签署人",
                "签署时间",
            )
        ],
    )
    for item in items:
        sheet.add(
            [
                item.experiment_id,
                lab.experiment_type(item.experiment_type),
                lab.state(item.state),
                item.instrument_no,
                item.operator,
                _stamp(item.created_at),
                _stamp(item.completed_at),
                item.tube_count,
                item.signed_by or "",
                _stamp(item.signed_at),
            ]
        )
    return _write_atomically(workbook, target), sheet.rows


def write_audit(
    records: Iterable[AuditRecord],
    destination: Path,
    labels: ExportLabels | None = None,
) -> tuple[Path, int]:
    """导出审计记录（序号、时间、操作者、权限、事件类型、操作记录、关联试验、哈希）。

    Args:
        records: 审计记录。
        destination: 目标文件。
        labels: 文字翻译。

    Returns:
        ``(写出的文件, 行数)``。
    """
    lab = labels or ExportLabels()
    t = lab.text
    target = _prepare(destination)
    workbook = _new_workbook()
    sheet = _SheetWriter(
        workbook,
        t("审计追踪"),
        [
            t(h)
            for h in ("序号", "时间", "操作者", "权限", "事件类型", "操作记录", "关联试验", "哈希")
        ],
    )
    for record in records:
        event = record.event
        sheet.add(
            [
                record.seq,
                _stamp(event.occurred_at),
                event.actor,
                "" if event.actor_role is None else lab.user_role(event.actor_role),
                event.action.value,
                event.detail,
                event.experiment_id or "",
                record.hash,
            ]
        )
    return _write_atomically(workbook, target), sheet.rows
