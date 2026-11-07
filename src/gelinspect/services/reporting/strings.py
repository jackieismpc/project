"""报告里的全部固定文字（简体中文原文）。

每个键对应一条原文，用 ``QT_TRANSLATE_NOOP("Report", …)`` 标记，让
``scripts/update_translations.py`` 能收集；英文译文在 ``translations/gelinspect_en.ts``，
测试保证每个键都有英文。使用时调用 :meth:`ReportTexts.s`。
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QT_TRANSLATE_NOOP


class S(StrEnum):
    """报告文字的键。"""

    TITLE = "title"
    BATCH_TITLE = "batch_title"
    FILE_NAME = "file_name"
    INSTRUMENT_NO = "instrument_no"
    TEST_DATE = "test_date"
    TEMPERATURE_RANGE = "temperature_range"
    OPERATOR = "operator"
    SIGNED_BY_DATE = "signed_by_date"
    UNSIGNED = "unsigned"
    EXPERIMENT_TYPE = "experiment_type"
    SENSITIVITY = "sensitivity"
    LAL_BATCH = "lal_batch"
    ENDOTOXIN_BATCH = "endotoxin_batch"
    WATER_BATCH = "water_batch"
    SAMPLE_BATCH = "sample_batch"
    MVD = "mvd"
    ENDOTOXIN_LIMIT = "endotoxin_limit"
    COL_TUBE = "col_tube"
    COL_SAMPLE = "col_sample"
    COL_SERIAL = "col_serial"
    COL_DILUTION = "col_dilution"
    COL_SPIKED = "col_spiked"
    COL_RESULT = "col_result"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    PENDING = "pending"
    NO_RESULT = "no_result"
    REVIEWED = "reviewed"
    CONCLUSION = "conclusion"
    NONE = "none"
    NO_CONCLUSION = "no_conclusion"
    MEASURED = "measured"
    NOT_FINAL_HINT = "not_final_hint"
    LAMBDA_C_REFERENCE = "lambda_c_reference"
    NOT_FINAL = "not_final"
    LABELED_LAMBDA = "labeled_lambda"
    REASON_INVALID = "reason_invalid"
    REASON_PENDING = "reason_pending"
    REASON_UNMET = "reason_unmet"
    BASIS = "basis"
    RULE_VERSION = "rule_version"
    RULE_SOURCE = "rule_source"
    REVIEWED_NOTE = "reviewed_note"
    IMAGES_TITLE = "images_title"
    ROW_N = "row_n"
    BEFORE = "before"
    AFTER = "after"
    NO_IMAGE = "no_image"
    COL_EXPERIMENT = "col_experiment"
    COL_COUNT = "col_count"
    COL_STATE = "col_state"
    EXPERIMENT = "experiment"
    EXPERIMENT_COUNT = "experiment_count"
    FOOT_SIGNED = "foot_signed"
    FOOT_UNSIGNED = "foot_unsigned"
    FOOT_COPYRIGHT = "foot_copyright"
    FOOT_DIGEST = "foot_digest"
    FOOT_PAGE = "foot_page"
    META_TITLE = "meta_title"
    META_SUBJECT = "meta_subject"
    SIGN_REASON = "sign_reason"
    HOLDER_PLACEHOLDER = "holder_placeholder"


SOURCES: dict[S, str] = {
    S.TITLE: str(QT_TRANSLATE_NOOP("Report", "自动凝胶检测报告")),
    S.BATCH_TITLE: str(QT_TRANSLATE_NOOP("Report", "单批次汇总报告")),
    S.FILE_NAME: str(QT_TRANSLATE_NOOP("Report", "文件名（试验编号）")),
    S.INSTRUMENT_NO: str(QT_TRANSLATE_NOOP("Report", "仪器编号")),
    S.TEST_DATE: str(QT_TRANSLATE_NOOP("Report", "检测日期")),
    S.TEMPERATURE_RANGE: str(QT_TRANSLATE_NOOP("Report", "温度范围")),
    S.OPERATOR: str(QT_TRANSLATE_NOOP("Report", "操作人")),
    S.SIGNED_BY_DATE: str(QT_TRANSLATE_NOOP("Report", "签署人 / 日期")),
    S.UNSIGNED: str(QT_TRANSLATE_NOOP("Report", "未签署")),
    S.EXPERIMENT_TYPE: str(QT_TRANSLATE_NOOP("Report", "试验类型")),
    S.SENSITIVITY: str(QT_TRANSLATE_NOOP("Report", "鲎试剂灵敏度")),
    S.LAL_BATCH: str(QT_TRANSLATE_NOOP("Report", "鲎试剂批号")),
    S.ENDOTOXIN_BATCH: str(QT_TRANSLATE_NOOP("Report", "内毒素批号")),
    S.WATER_BATCH: str(QT_TRANSLATE_NOOP("Report", "检测用水批号")),
    S.SAMPLE_BATCH: str(QT_TRANSLATE_NOOP("Report", "样品批号")),
    S.MVD: str(QT_TRANSLATE_NOOP("Report", "MVD")),
    S.ENDOTOXIN_LIMIT: str(QT_TRANSLATE_NOOP("Report", "内毒素限值")),
    S.COL_TUBE: str(QT_TRANSLATE_NOOP("Report", "试管编号")),
    S.COL_SAMPLE: str(QT_TRANSLATE_NOOP("Report", "样品名称")),
    S.COL_SERIAL: str(QT_TRANSLATE_NOOP("Report", "序列号")),
    S.COL_DILUTION: str(QT_TRANSLATE_NOOP("Report", "稀释倍数")),
    S.COL_SPIKED: str(QT_TRANSLATE_NOOP("Report", "外加内毒素（EU/mL）")),
    S.COL_RESULT: str(QT_TRANSLATE_NOOP("Report", "阴阳性")),
    S.POSITIVE: str(QT_TRANSLATE_NOOP("Report", "阳性")),
    S.NEGATIVE: str(QT_TRANSLATE_NOOP("Report", "阴性")),
    S.PENDING: str(QT_TRANSLATE_NOOP("Report", "待复核")),
    S.NO_RESULT: str(QT_TRANSLATE_NOOP("Report", "无结果")),
    S.REVIEWED: str(QT_TRANSLATE_NOOP("Report", "（已复核）")),
    S.CONCLUSION: str(QT_TRANSLATE_NOOP("Report", "结论")),
    S.NONE: str(QT_TRANSLATE_NOOP("Report", "无")),
    S.NO_CONCLUSION: str(
        QT_TRANSLATE_NOOP("Report", "这个试验完成时还没有结论功能，没有入库的结论。")
    ),
    S.MEASURED: str(QT_TRANSLATE_NOOP("Report", "实测灵敏度")),
    S.NOT_FINAL_HINT: str(QT_TRANSLATE_NOOP("Report", "（不是最终结论，不能作为放行依据）")),
    S.LAMBDA_C_REFERENCE: str(QT_TRANSLATE_NOOP("Report", "λc 参考值")),
    S.NOT_FINAL: str(QT_TRANSLATE_NOOP("Report", "不是最终结论")),
    S.LABELED_LAMBDA: str(QT_TRANSLATE_NOOP("Report", "标示灵敏度 λ")),
    S.REASON_INVALID: str(QT_TRANSLATE_NOOP("Report", "无效原因")),
    S.REASON_PENDING: str(QT_TRANSLATE_NOOP("Report", "待定原因")),
    S.REASON_UNMET: str(QT_TRANSLATE_NOOP("Report", "未满足的前提")),
    S.BASIS: str(QT_TRANSLATE_NOOP("Report", "依据（+ 阳性，− 阴性）")),
    S.RULE_VERSION: str(QT_TRANSLATE_NOOP("Report", "规则版本")),
    S.RULE_SOURCE: str(QT_TRANSLATE_NOOP("Report", "规则来源")),
    S.REVIEWED_NOTE: str(
        QT_TRANSLATE_NOOP("Report", "标注“已复核”的结果经分析员人工复核，以复核结果为准。")
    ),
    S.IMAGES_TITLE: str(QT_TRANSLATE_NOOP("Report", "8 连图（每排翻转前、翻转后代表帧）")),
    S.ROW_N: str(QT_TRANSLATE_NOOP("Report", "第 {row} 排")),
    S.BEFORE: str(QT_TRANSLATE_NOOP("Report", "翻转前")),
    S.AFTER: str(QT_TRANSLATE_NOOP("Report", "翻转后")),
    S.NO_IMAGE: str(QT_TRANSLATE_NOOP("Report", "没有图像")),
    S.COL_EXPERIMENT: str(QT_TRANSLATE_NOOP("Report", "试验编号")),
    S.COL_COUNT: str(QT_TRANSLATE_NOOP("Report", "试管数")),
    S.COL_STATE: str(QT_TRANSLATE_NOOP("Report", "状态")),
    S.EXPERIMENT: str(QT_TRANSLATE_NOOP("Report", "试验")),
    S.EXPERIMENT_COUNT: str(QT_TRANSLATE_NOOP("Report", "共 {n} 个试验")),
    S.FOOT_SIGNED: str(
        QT_TRANSLATE_NOOP("Report", "本报告已使用数字签名，任何修改都会导致验签失败。")
    ),
    S.FOOT_UNSIGNED: str(
        QT_TRANSLATE_NOOP("Report", "本报告尚未签署，仅供预览和核对，不具备签署报告的效力。")
    ),
    S.FOOT_COPYRIGHT: str(QT_TRANSLATE_NOOP("Report", "© {holder}，未经许可不得复制。")),
    S.FOOT_DIGEST: str(QT_TRANSLATE_NOOP("Report", "内容摘要 SHA-256")),
    S.FOOT_PAGE: str(QT_TRANSLATE_NOOP("Report", "第 {page} 页 / 共 {total} 页")),
    S.META_TITLE: str(QT_TRANSLATE_NOOP("Report", "自动凝胶检测报告 {id}")),
    S.META_SUBJECT: str(QT_TRANSLATE_NOOP("Report", "凝胶法细菌内毒素检测")),
    S.SIGN_REASON: str(QT_TRANSLATE_NOOP("Report", "签署报告 {id}")),
    S.HOLDER_PLACEHOLDER: str(QT_TRANSLATE_NOOP("Report", "[版权方名称]")),
}
"""键到简体中文原文。"""

DEFAULT_HOLDER = "[版权方名称]"
"""配置里版权权利人的默认值（占位文字）：报告按语言显示对应的占位文字。"""
