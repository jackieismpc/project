"""Excel 导出的文字：列标题和枚举值，按界面当前语言翻译。

服务层的 :class:`~gelinspect.services.excel_export.ExportLabels` 只接收翻译函数，不依赖 Qt。
这里登记全部源文本（``QT_TRANSLATE_NOOP``）让翻译工具收集；测试保证它们和服务层用到的一字不差。
"""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.enums import (
    GelState,
    Interpretation,
    ReviewStatus,
    TubeAnalysisStatus,
)
from gelinspect.services.excel_export import ExportLabels
from gelinspect.ui.labels import (
    conclusion_note_text,
    conclusion_verdict_label,
    experiment_state_label,
    experiment_type_label,
    role_label,
    tube_role_label,
)

_SOURCES: tuple[str, ...] = (
    str(QT_TRANSLATE_NOOP("Export", "试验")),
    str(QT_TRANSLATE_NOOP("Export", "单管结果")),
    str(QT_TRANSLATE_NOOP("Export", "结论依据")),
    str(QT_TRANSLATE_NOOP("Export", "历史列表")),
    str(QT_TRANSLATE_NOOP("Export", "审计追踪")),
    str(QT_TRANSLATE_NOOP("Export", "试验编号")),
    str(QT_TRANSLATE_NOOP("Export", "类型")),
    str(QT_TRANSLATE_NOOP("Export", "状态")),
    str(QT_TRANSLATE_NOOP("Export", "仪器编号")),
    str(QT_TRANSLATE_NOOP("Export", "操作人")),
    str(QT_TRANSLATE_NOOP("Export", "创建时间")),
    str(QT_TRANSLATE_NOOP("Export", "完成时间")),
    str(QT_TRANSLATE_NOOP("Export", "签署人")),
    str(QT_TRANSLATE_NOOP("Export", "签署时间")),
    str(QT_TRANSLATE_NOOP("Export", "鲎试剂批号")),
    str(QT_TRANSLATE_NOOP("Export", "内毒素批号")),
    str(QT_TRANSLATE_NOOP("Export", "检测用水批号")),
    str(QT_TRANSLATE_NOOP("Export", "标示灵敏度 λ（EU/mL）")),
    str(QT_TRANSLATE_NOOP("Export", "MVD")),
    str(QT_TRANSLATE_NOOP("Export", "内毒素限值")),
    str(QT_TRANSLATE_NOOP("Export", "实测灵敏度 λc（EU/mL）")),
    str(QT_TRANSLATE_NOOP("Export", "结论")),
    str(QT_TRANSLATE_NOOP("Export", "结论数值")),
    str(QT_TRANSLATE_NOOP("Export", "规则版本")),
    str(QT_TRANSLATE_NOOP("Export", "管数")),
    str(QT_TRANSLATE_NOOP("Export", "待复核管数")),
    str(QT_TRANSLATE_NOOP("Export", "管号")),
    str(QT_TRANSLATE_NOOP("Export", "排")),
    str(QT_TRANSLATE_NOOP("Export", "样品名称")),
    str(QT_TRANSLATE_NOOP("Export", "序列号")),
    str(QT_TRANSLATE_NOOP("Export", "样品批号")),
    str(QT_TRANSLATE_NOOP("Export", "稀释倍数")),
    str(QT_TRANSLATE_NOOP("Export", "外加内毒素（EU/mL）")),
    str(QT_TRANSLATE_NOOP("Export", "用途")),
    str(QT_TRANSLATE_NOOP("Export", "分析状态")),
    str(QT_TRANSLATE_NOOP("Export", "算法三态")),
    str(QT_TRANSLATE_NOOP("Export", "自动判读")),
    str(QT_TRANSLATE_NOOP("Export", "复核状态")),
    str(QT_TRANSLATE_NOOP("Export", "最终判读")),
    str(QT_TRANSLATE_NOOP("Export", "复核人")),
    str(QT_TRANSLATE_NOOP("Export", "复核时间")),
    str(QT_TRANSLATE_NOOP("Export", "复核备注")),
    str(QT_TRANSLATE_NOOP("Export", "评分")),
    str(QT_TRANSLATE_NOOP("Export", "置信度")),
    str(QT_TRANSLATE_NOOP("Export", "面积变化率 ΔA（%）")),
    str(QT_TRANSLATE_NOOP("Export", "轴向偏移 Δs")),
    str(QT_TRANSLATE_NOOP("Export", "面积时序标准差 σ_A")),
    str(QT_TRANSLATE_NOOP("Export", "轴向时序标准差 σ_s")),
    str(QT_TRANSLATE_NOOP("Export", "翻转前面积")),
    str(QT_TRANSLATE_NOOP("Export", "翻转后面积")),
    str(QT_TRANSLATE_NOOP("Export", "模型版本")),
    str(QT_TRANSLATE_NOOP("Export", "配置版本")),
    str(QT_TRANSLATE_NOOP("Export", "分析耗时（ms）")),
    str(QT_TRANSLATE_NOOP("Export", "算法说明")),
    str(QT_TRANSLATE_NOOP("Export", "类别")),
    str(QT_TRANSLATE_NOOP("Export", "内容")),
    str(QT_TRANSLATE_NOOP("Export", "依据")),
    str(QT_TRANSLATE_NOOP("Export", "未满足的前提")),
    str(QT_TRANSLATE_NOOP("Export", "序号")),
    str(QT_TRANSLATE_NOOP("Export", "时间")),
    str(QT_TRANSLATE_NOOP("Export", "操作者")),
    str(QT_TRANSLATE_NOOP("Export", "权限")),
    str(QT_TRANSLATE_NOOP("Export", "事件类型")),
    str(QT_TRANSLATE_NOOP("Export", "操作记录")),
    str(QT_TRANSLATE_NOOP("Export", "关联试验")),
    str(QT_TRANSLATE_NOOP("Export", "哈希")),
)
"""登记全部列标题和类别名，与 ``excel_export.EXPORT_TEXTS`` 必须一致。"""

_INTERPRETATIONS: dict[Interpretation, str] = {
    Interpretation.POSITIVE: str(QT_TRANSLATE_NOOP("ExportValue", "阳性")),
    Interpretation.NEGATIVE: str(QT_TRANSLATE_NOOP("ExportValue", "阴性")),
    Interpretation.PENDING_REVIEW: str(QT_TRANSLATE_NOOP("ExportValue", "待复核")),
}
_GEL_STATES: dict[GelState, str] = {
    GelState.FULLY_GELLED: str(QT_TRANSLATE_NOOP("ExportValue", "完全凝固")),
    GelState.PARTIALLY_GELLED: str(QT_TRANSLATE_NOOP("ExportValue", "部分凝固")),
    GelState.NOT_GELLED: str(QT_TRANSLATE_NOOP("ExportValue", "未凝固")),
}
_REVIEW: dict[ReviewStatus, str] = {
    ReviewStatus.NOT_REQUIRED: str(QT_TRANSLATE_NOOP("ExportValue", "无需复核")),
    ReviewStatus.PENDING: str(QT_TRANSLATE_NOOP("ExportValue", "等待复核")),
    ReviewStatus.REVIEWED: str(QT_TRANSLATE_NOOP("ExportValue", "已复核")),
}
_ANALYSIS: dict[TubeAnalysisStatus, str] = {
    TubeAnalysisStatus.OK: str(QT_TRANSLATE_NOOP("ExportValue", "分析成功")),
    TubeAnalysisStatus.GEL_MISSING: str(QT_TRANSLATE_NOOP("ExportValue", "凝胶缺失")),
    TubeAnalysisStatus.TUBE_NOT_FOUND: str(QT_TRANSLATE_NOOP("ExportValue", "未找到试管")),
    TubeAnalysisStatus.LOW_QUALITY: str(QT_TRANSLATE_NOOP("ExportValue", "图像质量不足")),
    TubeAnalysisStatus.FAILED: str(QT_TRANSLATE_NOOP("ExportValue", "分析失败")),
}


def _translate(source: str) -> str:
    return QCoreApplication.translate("Export", source)


def export_labels() -> ExportLabels:
    """按界面当前语言生成导出用的文字翻译。

    Returns:
        导出标签；在 ``QApplication`` 创建之后调用，语言切换后需要重新取。
    """
    return ExportLabels(
        text=_translate,
        state=experiment_state_label,
        experiment_type=experiment_type_label,
        role=tube_role_label,
        verdict=conclusion_verdict_label,
        interpretation=lambda v: QCoreApplication.translate("ExportValue", _INTERPRETATIONS[v]),
        gel_state=lambda v: QCoreApplication.translate("ExportValue", _GEL_STATES[v]),
        review_status=lambda v: QCoreApplication.translate("ExportValue", _REVIEW[v]),
        analysis_status=lambda v: QCoreApplication.translate("ExportValue", _ANALYSIS[v]),
        user_role=role_label,
        note=conclusion_note_text,
    )
