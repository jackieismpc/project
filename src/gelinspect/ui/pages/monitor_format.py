"""监控页和单管详情共用的显示转换：状态、倒计时、数值和富文本片段。"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.enums import GelState, Interpretation, TubeAnalysisStatus
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.engine_types import RowPhase, WellStatus
from gelinspect.services.monitor_service import ExperimentView
from gelinspect.ui.labels import experiment_state_label, experiment_type_label
from gelinspect.ui.theme.fonts import load_fonts
from gelinspect.ui.widgets.badge import BadgeTone
from gelinspect.ui.widgets.marks import WellState

_STATUS_STATE = {
    WellStatus.INCUBATING: WellState.INCUBATING,
    WellStatus.READING: WellState.READING,
    WellStatus.ANALYZING: WellState.READING,
    WellStatus.POSITIVE: WellState.POSITIVE,
    WellStatus.NEGATIVE: WellState.NEGATIVE,
    WellStatus.PENDING_REVIEW: WellState.REVIEW,
    WellStatus.ERROR: WellState.ERROR,
}
_INTERPRETATION_STATE = {
    Interpretation.POSITIVE: WellState.POSITIVE,
    Interpretation.NEGATIVE: WellState.NEGATIVE,
    Interpretation.PENDING_REVIEW: WellState.REVIEW,
}

_INTERPRETATION_NAMES = {
    Interpretation.POSITIVE: str(QT_TRANSLATE_NOOP("Interpretation", "阳性")),
    Interpretation.NEGATIVE: str(QT_TRANSLATE_NOOP("Interpretation", "阴性")),
    Interpretation.PENDING_REVIEW: str(QT_TRANSLATE_NOOP("Interpretation", "待复核")),
}
_GEL_NAMES = {
    GelState.FULLY_GELLED: str(QT_TRANSLATE_NOOP("GelState", "完全凝固")),
    GelState.PARTIALLY_GELLED: str(QT_TRANSLATE_NOOP("GelState", "部分凝固")),
    GelState.NOT_GELLED: str(QT_TRANSLATE_NOOP("GelState", "未凝固")),
}
_STATUS_NAMES = {
    TubeAnalysisStatus.OK: str(QT_TRANSLATE_NOOP("AnalysisStatus", "分析成功")),
    TubeAnalysisStatus.GEL_MISSING: str(QT_TRANSLATE_NOOP("AnalysisStatus", "凝胶缺失")),
    TubeAnalysisStatus.TUBE_NOT_FOUND: str(QT_TRANSLATE_NOOP("AnalysisStatus", "未找到试管")),
    TubeAnalysisStatus.LOW_QUALITY: str(QT_TRANSLATE_NOOP("AnalysisStatus", "图像质量不足")),
    TubeAnalysisStatus.FAILED: str(QT_TRANSLATE_NOOP("AnalysisStatus", "分析失败")),
}


def well_state(status: WellStatus | None) -> WellState:
    """引擎的孔状态转成控件的显示状态。

    Args:
        status: 引擎孔状态，None 表示没有结果。

    Returns:
        显示状态。
    """
    return WellState.EMPTY if status is None else _STATUS_STATE[status]


def interpretation_state(value: Interpretation) -> WellState:
    """判读转成显示状态。

    Args:
        value: 判读。

    Returns:
        显示状态。
    """
    return _INTERPRETATION_STATE[value]


def interpretation_label(value: Interpretation) -> str:
    """判读的当前语言名称。

    Args:
        value: 判读。

    Returns:
        例如“阳性”。
    """
    return QCoreApplication.translate("Interpretation", _INTERPRETATION_NAMES[value])


def gel_state_label(value: GelState | None) -> str:
    """算法三态的当前语言名称。

    Args:
        value: 三态，无法判定为 None。

    Returns:
        例如“部分凝固”。
    """
    if value is None:
        return QCoreApplication.translate("GelState", "无法判定")
    return QCoreApplication.translate("GelState", _GEL_NAMES[value])


def analysis_status_label(value: TubeAnalysisStatus) -> str:
    """算法处理状态的当前语言名称。

    Args:
        value: 处理状态。

    Returns:
        例如“图像质量不足”。
    """
    return QCoreApplication.translate("AnalysisStatus", _STATUS_NAMES[value])


def countdown(phase: RowPhase | None, remaining_s: float | None, *, done: bool) -> tuple[str, bool]:
    """孔板列头下的倒计时文字。

    Args:
        phase: 这一排的阶段，试验不在运行时为 None。
        remaining_s: 距到时的设备时间。
        done: 不在运行时，这一排是否已有全部结果。

    Returns:
        ``(文字, 是否为活动排)``，文字为空表示不显示。
    """
    if phase is None:
        return (QCoreApplication.translate("PlateGrid", "已读数"), False) if done else ("", False)
    if phase is RowPhase.WAITING:
        total = max(0, round(remaining_s or 0.0))
        return f"{total // 60:02d}:{total % 60:02d}", False
    if phase is RowPhase.READING:
        return QCoreApplication.translate("PlateGrid", "读数中"), True
    if phase is RowPhase.ANALYZING:
        return QCoreApplication.translate("PlateGrid", "分析中"), True
    if phase is RowPhase.READ_FAILED:
        return QCoreApplication.translate("PlateGrid", "读数失败"), True
    if phase is RowPhase.ANALYSIS_FAILED:
        return QCoreApplication.translate("PlateGrid", "分析失败"), True
    return QCoreApplication.translate("PlateGrid", "已读数"), False


def status_badge(view: ExperimentView) -> tuple[str, BadgeTone]:
    """标题行状态徽标的文字和色调。

    Args:
        view: 试验视图。

    Returns:
        ``(文字, 色调)``。
    """
    active = next((r for r in view.rows if r.phase in (RowPhase.READING, RowPhase.ANALYZING)), None)
    state = view.state
    if state is ExperimentState.READING and active is not None:
        return QCoreApplication.translate("MonitorPage", "第 {n} 排读数中").format(
            n=active.row_index
        ), BadgeTone.INFO
    if state is ExperimentState.ANALYZING and active is not None:
        return QCoreApplication.translate("MonitorPage", "第 {n} 排分析中").format(
            n=active.row_index
        ), BadgeTone.INFO
    if state is ExperimentState.PENDING_REVIEW:
        text = QCoreApplication.translate("MonitorPage", "待复核 · {n} 管").format(
            n=len(view.pending_review)
        )
        return text, BadgeTone.REVIEW
    tone = {
        ExperimentState.COMPLETED: BadgeTone.SUCCESS,
        ExperimentState.SIGNED: BadgeTone.SUCCESS,
        ExperimentState.FAULTED: BadgeTone.DANGER,
        ExperimentState.STOPPED: BadgeTone.NEUTRAL,
    }.get(state, BadgeTone.INFO)
    return experiment_state_label(state), tone


def mono_span(text: str) -> str:
    """等宽字体的富文本片段。

    Args:
        text: 文字（会转义）。

    Returns:
        可嵌入 ``QLabel`` 富文本的 HTML。
    """
    return f"<span style=\"font-family:'{load_fonts().mono}'\">{escape(text)}</span>"


def number(value: float | None, digits: int = 2, unit: str = "") -> str:
    """格式化数值，缺失时为短横线。

    Args:
        value: 数值。
        digits: 小数位数。
        unit: 单位。

    Returns:
        例如 ``38.60 %``。
    """
    if value is None:
        return "–"
    return f"{value:.{digits}f}{unit}"


def experiment_title(view: ExperimentView) -> str:
    """试验的显示名称：内置模板按类型翻译，自定义模板原样显示。

    Args:
        view: 试验视图。

    Returns:
        例如“灵敏度复核”。
    """
    if view.template_name and not view.template_builtin:
        return view.template_name
    return experiment_type_label(view.experiment_type)
