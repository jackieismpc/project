"""领域枚举。

枚举值会写入数据库、配置和审计日志，属于稳定契约，修改已有值需要数据迁移。
"""

from __future__ import annotations

from enum import StrEnum


class ExperimentType(StrEnum):
    """试验类型。"""

    SENSITIVITY_CONFIRMATION = "sensitivity_confirmation"
    """灵敏度复核。"""
    INTERFERENCE = "interference"
    """干扰试验。"""
    RETEST = "retest"
    """复试试验。"""
    SEMI_QUANTITATIVE = "semi_quantitative"
    """半定量试验。"""
    LIMIT = "limit"
    """限度试验。"""


class TubeRole(StrEnum):
    """模板中一支试管的用途。"""

    TEST = "test"
    """试验管。"""
    NEGATIVE_CONTROL = "negative_control"
    """阴性对照。"""
    UNCONFIRMED = "unconfirmed"
    """用途未确认，内置推定模板使用，等待甲方 QA 核对。"""


class GelState(StrEnum):
    """算法输出的凝固三态。"""

    FULLY_GELLED = "fully_gelled"
    """完全凝固。"""
    PARTIALLY_GELLED = "partially_gelled"
    """部分凝固。"""
    NOT_GELLED = "not_gelled"
    """未凝固。"""


class TubeAnalysisStatus(StrEnum):
    """算法对单支试管的处理状态。"""

    OK = "ok"
    """分析成功，``state`` 必有值。"""
    GEL_MISSING = "gel_missing"
    """翻转前或翻转后凝胶缺失（面积低于算法阈值）。"""
    TUBE_NOT_FOUND = "tube_not_found"
    """未找到试管，无法分析。"""
    LOW_QUALITY = "low_quality"
    """图像质量不足，结果不可靠。"""
    FAILED = "failed"
    """该支试管分析失败。"""


class Interpretation(StrEnum):
    """软件侧的判读结果。算法不输出此枚举。"""

    POSITIVE = "positive"
    """阳性。"""
    NEGATIVE = "negative"
    """阴性。"""
    PENDING_REVIEW = "pending_review"
    """待复核，人工确认前不能签署。"""


class ReviewStatus(StrEnum):
    """单管人工复核状态。"""

    NOT_REQUIRED = "not_required"
    """自动判读已有明确结果，无需复核。"""
    PENDING = "pending"
    """等待人工复核。"""
    REVIEWED = "reviewed"
    """已人工复核，以复核结论为准。"""


class ReagentKind(StrEnum):
    """试验所需的试剂种类，对应报告中的三种批号。"""

    LAL_REAGENT = "lal_reagent"
    """鲎试剂。"""
    ENDOTOXIN = "endotoxin"
    """内毒素。"""
    WATER = "water"
    """检测用水。"""


class ReagentCheckStatus(StrEnum):
    """试剂校验结果。除 ``VALID`` 外都会锁定试验。"""

    VALID = "valid"
    """在有效期内且已备案。"""
    EXPIRED = "expired"
    """已过期。"""
    NOT_REGISTERED = "not_registered"
    """未备案。"""
    EXPIRY_UNKNOWN = "expiry_unknown"
    """有效期未知，按不合格处理。"""
    MISSING = "missing"
    """缺少该种类的试剂批次。"""


class Role(StrEnum):
    """用户角色。"""

    ADMIN = "admin"
    """管理员。"""
    EXPERIMENTER = "experimenter"
    """实验员。"""
    ANALYST = "analyst"
    """分析员。"""


class AuditAction(StrEnum):
    """审计事件类型。"""

    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    USER_CHANGED = "user_changed"
    SETTINGS_CHANGED = "settings_changed"
    TEMPLATE_CHANGED = "template_changed"
    REAGENT_CHECKED = "reagent_checked"
    EXPERIMENT_CREATED = "experiment_created"
    EXPERIMENT_STATE_CHANGED = "experiment_state_changed"
    ROW_ANALYZED = "row_analyzed"
    ANALYSIS_TIMEOUT = "analysis_timeout"
    ENGINE_ALERT = "engine_alert"
    TUBE_REVIEWED = "tube_reviewed"
    EXPERIMENT_SIGNED = "experiment_signed"
    REPORT_EXPORTED = "report_exported"
    DATA_EXPORTED = "data_exported"
