"""领域枚举在界面上的显示名称，随语言切换。

源文本是简体中文，用 ``QT_TRANSLATE_NOOP`` 标记，显示时经 ``QCoreApplication.translate``。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.conclusions import (
    NOTE_TEMPLATES,
    ConclusionNote,
    format_quantity,
)
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    ReagentKind,
    Role,
    TubeRole,
)
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import AuthError, AuthErrorCode
from gelinspect.services.engine_types import AlertCode
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.reagent_service import ReagentLockedError, ReagentScan, ScanOutcome
from gelinspect.services.settings_service import SettingsError
from gelinspect.services.user_settings import SettingsProblem, SettingsProblemCode
from gelinspect.services.video.events import VideoEventCode

_ROLES: dict[Role, str] = {
    Role.ADMIN: str(QT_TRANSLATE_NOOP("Role", "管理员")),
    Role.EXPERIMENTER: str(QT_TRANSLATE_NOOP("Role", "实验员")),
    Role.ANALYST: str(QT_TRANSLATE_NOOP("Role", "分析员")),
}

_EXPERIMENT_TYPES: dict[ExperimentType, str] = {
    ExperimentType.SENSITIVITY_CONFIRMATION: str(QT_TRANSLATE_NOOP("ExperimentType", "灵敏度复核")),
    ExperimentType.INTERFERENCE: str(QT_TRANSLATE_NOOP("ExperimentType", "干扰试验")),
    ExperimentType.RETEST: str(QT_TRANSLATE_NOOP("ExperimentType", "复试试验")),
    ExperimentType.SEMI_QUANTITATIVE: str(QT_TRANSLATE_NOOP("ExperimentType", "半定量试验")),
    ExperimentType.LIMIT: str(QT_TRANSLATE_NOOP("ExperimentType", "限度试验")),
}

_EXPERIMENT_STATES: dict[ExperimentState, str] = {
    ExperimentState.DRAFT: str(QT_TRANSLATE_NOOP("ExperimentState", "草稿")),
    ExperimentState.REAGENT_CHECK: str(QT_TRANSLATE_NOOP("ExperimentState", "试剂校验中")),
    ExperimentState.REAGENT_LOCKED: str(QT_TRANSLATE_NOOP("ExperimentState", "试剂已锁定")),
    ExperimentState.READY: str(QT_TRANSLATE_NOOP("ExperimentState", "就绪")),
    ExperimentState.INCUBATING: str(QT_TRANSLATE_NOOP("ExperimentState", "孵育中")),
    ExperimentState.READING: str(QT_TRANSLATE_NOOP("ExperimentState", "读数中")),
    ExperimentState.ANALYZING: str(QT_TRANSLATE_NOOP("ExperimentState", "分析中")),
    ExperimentState.PENDING_REVIEW: str(QT_TRANSLATE_NOOP("ExperimentState", "待复核")),
    ExperimentState.COMPLETED: str(QT_TRANSLATE_NOOP("ExperimentState", "已完成")),
    ExperimentState.SIGNED: str(QT_TRANSLATE_NOOP("ExperimentState", "已签署")),
    ExperimentState.STOPPED: str(QT_TRANSLATE_NOOP("ExperimentState", "已停止")),
    ExperimentState.FAULTED: str(QT_TRANSLATE_NOOP("ExperimentState", "异常")),
}


def role_label(role: Role) -> str:
    """角色的当前语言名称。

    Args:
        role: 角色。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("Role", _ROLES[role])


def experiment_type_label(value: ExperimentType) -> str:
    """试验类型的当前语言名称。

    Args:
        value: 试验类型。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("ExperimentType", _EXPERIMENT_TYPES[value])


def experiment_state_label(value: ExperimentState) -> str:
    """试验状态的当前语言名称。

    Args:
        value: 试验状态。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("ExperimentState", _EXPERIMENT_STATES[value])


_AUTH_MESSAGES: dict[AuthErrorCode, str] = {
    AuthErrorCode.INVALID_CREDENTIALS: str(
        QT_TRANSLATE_NOOP("AuthError", "用户名或密码错误，还可以尝试 {remaining} 次")
    ),
    AuthErrorCode.LOCKED: str(
        QT_TRANSLATE_NOOP("AuthError", "失败次数过多，已暂时锁定，请 {minutes} 分钟后再试")
    ),
    AuthErrorCode.DISABLED: str(QT_TRANSLATE_NOOP("AuthError", "账号已停用，请联系管理员")),
    AuthErrorCode.USERNAME_INVALID: str(
        QT_TRANSLATE_NOOP("AuthError", "登录名不能为空、不能含空白，且不超过 {max} 个字符")
    ),
    AuthErrorCode.PASSWORD_TOO_SHORT: str(
        QT_TRANSLATE_NOOP("AuthError", "密码不能少于 {min} 个字符")
    ),
    AuthErrorCode.PASSWORD_SAME_AS_OLD: str(
        QT_TRANSLATE_NOOP("AuthError", "新密码不能与当前密码相同")
    ),
    AuthErrorCode.PASSWORD_SAME_AS_USERNAME: str(
        QT_TRANSLATE_NOOP("AuthError", "新密码不能与登录名相同")
    ),
    AuthErrorCode.NOT_LOGGED_IN: str(QT_TRANSLATE_NOOP("AuthError", "请先登录")),
    AuthErrorCode.SESSION_INVALID: str(QT_TRANSLATE_NOOP("AuthError", "会话已失效，请重新登录")),
    AuthErrorCode.SCREEN_LOCKED: str(QT_TRANSLATE_NOOP("AuthError", "屏幕已锁定，请先解锁")),
    AuthErrorCode.MUST_CHANGE_PASSWORD: str(QT_TRANSLATE_NOOP("AuthError", "请先修改初始密码")),
    AuthErrorCode.DENIED: str(QT_TRANSLATE_NOOP("AuthError", "当前角色无此权限")),
    AuthErrorCode.ALREADY_INITIALIZED: str(
        QT_TRANSLATE_NOOP("AuthError", "系统已经初始化，不能再创建首个管理员")
    ),
    AuthErrorCode.SETUP_DISABLED: str(
        QT_TRANSLATE_NOOP("AuthError", "当前配置不允许在界面创建管理员")
    ),
    AuthErrorCode.STORAGE_BUSY: str(
        QT_TRANSLATE_NOOP("AuthError", "数据库正忙，操作没有生效，请稍后再试")
    ),
}


def describe_auth_error(error: AuthError) -> str:
    """把账号相关异常翻译成当前语言的提示。

    服务层的异常信息是简体中文（写审计和日志用），界面按错误分类和参数重新生成提示。

    Args:
        error: 服务层抛出的异常。

    Returns:
        面向用户的提示。
    """
    params = dict(error.params)
    if error.code is AuthErrorCode.LOCKED:
        seconds = float(params.pop("retry_after_s", 0.0))
        params["minutes"] = max(1, math.ceil(seconds / 60))
    template = QCoreApplication.translate("AuthError", _AUTH_MESSAGES[error.code])
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


_REAGENT_KINDS: dict[ReagentKind, str] = {
    ReagentKind.LAL_REAGENT: str(QT_TRANSLATE_NOOP("ReagentKind", "鲎试剂")),
    ReagentKind.ENDOTOXIN: str(QT_TRANSLATE_NOOP("ReagentKind", "内毒素")),
    ReagentKind.WATER: str(QT_TRANSLATE_NOOP("ReagentKind", "检查用水")),
}

_TUBE_ROLES: dict[TubeRole, str] = {
    TubeRole.TEST: str(QT_TRANSLATE_NOOP("TubeRole", "试验管")),
    TubeRole.NEGATIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "阴性对照")),
    TubeRole.SAMPLE: str(QT_TRANSLATE_NOOP("TubeRole", "供试品")),
    TubeRole.POSITIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "阳性对照")),
    TubeRole.SAMPLE_POSITIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "供试品阳性对照")),
    TubeRole.UNCONFIRMED: str(QT_TRANSLATE_NOOP("TubeRole", "用途待确认")),
}


def reagent_kind_label(kind: ReagentKind) -> str:
    """试剂种类的当前语言名称。

    Args:
        kind: 试剂种类。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("ReagentKind", _REAGENT_KINDS[kind])


def tube_role_label(role: TubeRole) -> str:
    """试管用途的当前语言名称。

    Args:
        role: 试管用途。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("TubeRole", _TUBE_ROLES[role])


STORAGE_BUSY_TEXT = str(QT_TRANSLATE_NOOP("ServiceError", "数据库正忙，操作没有生效，请稍后再试"))
"""数据库等写锁超时（``StorageBusyError``）时的提示源文本。"""


def describe_storage_busy() -> str:
    """数据库忙时的提示，当前语言。

    Returns:
        提示文字。
    """
    return QCoreApplication.translate("ServiceError", STORAGE_BUSY_TEXT)


_SCAN_MESSAGES: dict[ScanOutcome, str] = {
    ScanOutcome.VALID: str(QT_TRANSLATE_NOOP("ReagentScan", "已备案")),
    ScanOutcome.EXPIRED: str(QT_TRANSLATE_NOOP("ReagentScan", "已过期")),
    ScanOutcome.NOT_REGISTERED: str(QT_TRANSLATE_NOOP("ReagentScan", "未备案")),
    ScanOutcome.EXPIRY_UNKNOWN: str(
        QT_TRANSLATE_NOOP("ReagentScan", "有效期未知，无法确认在有效期内")
    ),
    ScanOutcome.KIND_MISMATCH: str(
        QT_TRANSLATE_NOOP("ReagentScan", "这是{found}，不是{expected}，请扫对应的试剂")
    ),
    ScanOutcome.UNREADABLE: str(QT_TRANSLATE_NOOP("ReagentScan", "无法识别这个码，请重新扫码")),
    ScanOutcome.REGISTRY_UNAVAILABLE: str(
        QT_TRANSLATE_NOOP("ReagentScan", "备案清单不可用，无法确认试剂合格")
    ),
    ScanOutcome.NOT_CONFIGURED: str(
        QT_TRANSLATE_NOOP("ReagentScan", "未配置试剂码解析或备案清单，无法确认试剂合格")
    ),
}


def describe_scan(scan: ReagentScan, expected: ReagentKind | None = None) -> str:
    """一次扫码校验结论的当前语言说明。

    Args:
        scan: 校验结果。
        expected: 期望的试剂种类（种类放错时用于说明）。

    Returns:
        例如「已备案 · 效期至 2027-06-30」「已于 2026-01-31 过期」。
    """
    template = QCoreApplication.translate("ReagentScan", _SCAN_MESSAGES[scan.outcome])
    if scan.outcome is ScanOutcome.VALID:
        if scan.expiry_date is None:
            return template
        until = QCoreApplication.translate("ReagentScan", "效期至 {date}")
        return f"{template} · {until.format(date=scan.expiry_date.isoformat())}"
    if scan.outcome is ScanOutcome.EXPIRED and scan.expiry_date is not None:
        return QCoreApplication.translate("ReagentScan", "已于 {date} 过期").format(
            date=scan.expiry_date.isoformat()
        )
    if scan.outcome is ScanOutcome.KIND_MISMATCH:
        found = reagent_kind_label(scan.kind) if scan.kind is not None else "?"
        wanted = reagent_kind_label(expected) if expected is not None else "?"
        return template.format(found=found, expected=wanted)
    return template


def describe_days_remaining(days: int | None) -> str:
    """距有效期天数的当前语言说明。

    Args:
        days: 天数（有效期当天为 0，过期为负）；未知时为 None。

    Returns:
        例如「剩余 237 天」「今天到期」「已过期 279 天」；未知时为空串。
    """
    if days is None:
        return ""
    if days > 0:
        return QCoreApplication.translate("ReagentScan", "剩余 {n} 天").format(n=days)
    if days == 0:
        return QCoreApplication.translate("ReagentScan", "今天到期")
    return QCoreApplication.translate("ReagentScan", "已过期 {n} 天").format(n=-days)


def describe_lock_reasons(error: ReagentLockedError) -> list[str]:
    """试剂锁定的原因，每种不合格的试剂一行，当前语言。

    Args:
        error: 锁定异常。

    Returns:
        形如「鲎试剂 LAL2501B：已于 2026-01-31 过期」的行。
    """
    lines: list[str] = []
    for kind, scan in error.scans.items():
        if scan.valid:
            continue
        batch = f" {scan.batch_no}" if scan.batch_no else ""
        lines.append(f"{reagent_kind_label(kind)}{batch}：{describe_scan(scan, kind)}")
    return lines


_SERVICE_MESSAGES: dict[ServiceErrorCode, str] = {
    ServiceErrorCode.TEMPLATE_NOT_FOUND: str(QT_TRANSLATE_NOOP("ServiceError", "模板不存在")),
    ServiceErrorCode.TEMPLATE_READ_ONLY: str(
        QT_TRANSLATE_NOOP("ServiceError", "内置模板 {name} 只读，请复制后再编辑")
    ),
    ServiceErrorCode.TEMPLATE_NAME_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "模板名称不能为空")
    ),
    ServiceErrorCode.TEMPLATE_NAME_DUPLICATE: str(
        QT_TRANSLATE_NOOP("ServiceError", "已有同名模板：{name}")
    ),
    ServiceErrorCode.TEMPLATE_NAME_TOO_LONG: str(
        QT_TRANSLATE_NOOP("ServiceError", "模板名称不能超过 {max} 个字符")
    ),
    ServiceErrorCode.TEMPLATE_TUBE_COUNT: str(
        QT_TRANSLATE_NOOP("ServiceError", "模板的试管数应在 1 到 {max} 之间")
    ),
    ServiceErrorCode.TEMPLATE_SAMPLE_NAME_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {index} 支试管的样品名称不能为空")
    ),
    ServiceErrorCode.TEMPLATE_VALUE_INVALID: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {index} 支试管的数值不合法（不能为负数）")
    ),
    ServiceErrorCode.DRAFT_TEMPLATE_MISSING: str(
        QT_TRANSLATE_NOOP("ServiceError", "请先选择检测模板")
    ),
    ServiceErrorCode.DRAFT_COUNT_MISMATCH: str(
        QT_TRANSLATE_NOOP("ServiceError", "模板要求 {expected} 支试管，实际安排了 {actual} 支")
    ),
    ServiceErrorCode.DRAFT_TUBES_INVALID: str(QT_TRANSLATE_NOOP("ServiceError", "管位不合法")),
    ServiceErrorCode.DRAFT_SAMPLE_NAME_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {index} 行的样品名称为空")
    ),
    ServiceErrorCode.EXPERIMENT_NOT_FOUND: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验不存在：{id}")
    ),
    ServiceErrorCode.EXPERIMENT_NOT_READY: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验 {id} 不是就绪状态，不能启动")
    ),
    ServiceErrorCode.ENGINE_BUSY: str(
        QT_TRANSLATE_NOOP("ServiceError", "已有试验正在运行，设备同一时间只能运行一个试验")
    ),
    ServiceErrorCode.ENGINE_REFUSED: str(QT_TRANSLATE_NOOP("ServiceError", "引擎拒绝启动试验")),
    ServiceErrorCode.STOP_NOT_ALLOWED: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验 {id} 当前状态不能停止")
    ),
    ServiceErrorCode.TUBE_NOT_FOUND: str(QT_TRANSLATE_NOOP("ServiceError", "{tube} 号管没有结果")),
    ServiceErrorCode.REVIEW_NOTE_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "复核意见必填，且不超过 {max} 个字符")
    ),
    ServiceErrorCode.REVIEW_NOT_ALLOWED: str(
        QT_TRANSLATE_NOOP("ServiceError", "全部排读数分析完成、试验进入待复核后才能复核")
    ),
    ServiceErrorCode.REVIEW_CONCLUSION_INVALID: str(
        QT_TRANSLATE_NOOP("ServiceError", "复核结论只能是阳性或阴性")
    ),
    ServiceErrorCode.REVIEW_CONFLICT: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验状态已被其他操作改变，请刷新后重试")
    ),
    ServiceErrorCode.RESUME_NOT_ALLOWED: str(
        QT_TRANSLATE_NOOP("ServiceError", "这个试验现在不能恢复，软件重启后只能重试分析或停止")
    ),
    ServiceErrorCode.RETRY_NOT_ALLOWED: str(
        QT_TRANSLATE_NOOP("ServiceError", "这个试验现在不能重试分析")
    ),
    ServiceErrorCode.LAMBDA_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "这个模板需要鲎试剂的标示灵敏度 λ，当前试剂没有提供")
    ),
    ServiceErrorCode.MEDIA_MISSING: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {row} 排没有可播放的录像")
    ),
    ServiceErrorCode.MEDIA_TAMPERED: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {row} 排的录像与入库的哈希不符，可能已被改动")
    ),
    ServiceErrorCode.REAGENT_LOCKED: str(
        QT_TRANSLATE_NOOP("ServiceError", "试剂未通过校验，流程已锁定")
    ),
    ServiceErrorCode.EXPERIMENT_SIGNED: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验已签署，结果和复核已锁定，不能再修改")
    ),
    ServiceErrorCode.SIGN_NOT_ALLOWED: str(
        QT_TRANSLATE_NOOP("ServiceError", "现在不能签署这个试验")
    ),
    ServiceErrorCode.SIGN_FAILED: str(
        QT_TRANSLATE_NOOP("ServiceError", "签署失败，没有生成报告，试验状态没有改变：{reason}")
    ),
    ServiceErrorCode.SIGN_CONFLICT: str(
        QT_TRANSLATE_NOOP("ServiceError", "试验状态已被其他操作改变，请刷新后重试")
    ),
    ServiceErrorCode.REPORT_NOT_FOUND: str(QT_TRANSLATE_NOOP("ServiceError", "报告不存在")),
    ServiceErrorCode.REPORT_FILE_MISSING: str(
        QT_TRANSLATE_NOOP("ServiceError", "报告文件不存在：{path}")
    ),
    ServiceErrorCode.REPORT_HASH_MISMATCH: str(
        QT_TRANSLATE_NOOP("ServiceError", "报告文件与入库的 SHA-256 不符，可能已被改动：{name}")
    ),
    ServiceErrorCode.BATCH_EMPTY: str(
        QT_TRANSLATE_NOOP("ServiceError", "这个批号下没有已完成的试验")
    ),
    ServiceErrorCode.BATCH_NOT_SIGNABLE: str(
        QT_TRANSLATE_NOOP("ServiceError", "单批次报告只能在其中全部试验都已签署后签署")
    ),
    ServiceErrorCode.EXPORT_EMPTY: str(QT_TRANSLATE_NOOP("ServiceError", "没有可导出的报告")),
    ServiceErrorCode.EXPORT_FAILED: str(QT_TRANSLATE_NOOP("ServiceError", "导出失败：{reason}")),
    ServiceErrorCode.EXPORT_PASSWORD_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "已开启导出加密，请设置导出密码")
    ),
    ServiceErrorCode.EXPORT_PASSWORD_WEAK: str(
        QT_TRANSLATE_NOOP("ServiceError", "导出密码至少 {min} 个字符")
    ),
    ServiceErrorCode.USER_NOT_FOUND: str(QT_TRANSLATE_NOOP("ServiceError", "用户不存在")),
    ServiceErrorCode.USER_DUPLICATE: str(QT_TRANSLATE_NOOP("ServiceError", "登录名已存在：{name}")),
    ServiceErrorCode.USER_LAST_ADMIN: str(
        QT_TRANSLATE_NOOP("ServiceError", "不能停用或降级最后一个管理员，否则没有人能管理用户")
    ),
    ServiceErrorCode.USER_SELF_DISABLE: str(
        QT_TRANSLATE_NOOP("ServiceError", "不能停用自己的账号")
    ),
    ServiceErrorCode.USER_SELF_RESET: str(
        QT_TRANSLATE_NOOP("ServiceError", "不能在这里重置自己的密码，请用用户菜单里的「修改密码」")
    ),
    ServiceErrorCode.USER_NAME_REQUIRED: str(
        QT_TRANSLATE_NOOP("ServiceError", "显示名称不能超过 {max} 个字符")
    ),
    ServiceErrorCode.SETTINGS_INVALID: str(QT_TRANSLATE_NOOP("ServiceError", "设置有误，没有保存")),
    ServiceErrorCode.SETTINGS_BUSY: str(
        QT_TRANSLATE_NOOP(
            "ServiceError", "试验正在运行，现在不能修改影响运行的设置（孵育、读数、温度、视频输入）"
        )
    ),
    ServiceErrorCode.SETTINGS_SAVE_FAILED: str(
        QT_TRANSLATE_NOOP("ServiceError", "设置没有保存：{reason}")
    ),
    ServiceErrorCode.SETTINGS_PREVIEW_FAILED: str(
        QT_TRANSLATE_NOOP("ServiceError", "无法读取视频输入：{reason}")
    ),
    ServiceErrorCode.LIMITS_INVALID: str(
        QT_TRANSLATE_NOOP("ServiceError", "「{field}」必须是大于 0 的数字")
    ),
    ServiceErrorCode.AUDIT_QUERY_INVALID: str(
        QT_TRANSLATE_NOOP("ServiceError", "起始时间必须早于结束时间")
    ),
}

_LIMIT_FIELDS: dict[str, str] = {
    "mvd": str(QT_TRANSLATE_NOOP("LimitField", "MVD")),
    "endotoxin_limit": str(QT_TRANSLATE_NOOP("LimitField", "内毒素限值")),
}


def describe_service_error(error: ServiceError) -> str:
    """业务服务错误的当前语言说明。

    服务层的异常信息是简体中文（写审计和日志用），界面按错误分类和参数重新生成提示。

    Args:
        error: 服务层抛出的异常。

    Returns:
        面向用户的提示。
    """
    template = QCoreApplication.translate("ServiceError", _SERVICE_MESSAGES[error.code])
    if isinstance(error, ReagentLockedError):
        reasons = "；".join(describe_lock_reasons(error))
        return f"{template}：{reasons}" if reasons else template
    params = {"max": 1000, **error.params}
    if error.code is ServiceErrorCode.LIMITS_INVALID and str(params.get("field")) in _LIMIT_FIELDS:
        params["field"] = QCoreApplication.translate(
            "LimitField", _LIMIT_FIELDS[str(params["field"])]
        )
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


_VERDICTS = {
    ConclusionVerdict.PASS: str(QT_TRANSLATE_NOOP("Conclusion", "合格")),
    ConclusionVerdict.FAIL: str(QT_TRANSLATE_NOOP("Conclusion", "不合格")),
    ConclusionVerdict.INVALID: str(QT_TRANSLATE_NOOP("Conclusion", "无效")),
    ConclusionVerdict.PENDING: str(QT_TRANSLATE_NOOP("Conclusion", "待定")),
}
_RULE_SOURCES = (
    str(QT_TRANSLATE_NOOP("Conclusion", "项目任务书；中国药典通则 1143（条文原文待 QA 逐字核对）")),
    str(QT_TRANSLATE_NOOP("Conclusion", "中国药典通则 1143，待 QA 核对")),
)
_NOTE_SOURCES = (
    str(QT_TRANSLATE_NOOP("Conclusion", "标示灵敏度 λ = {lam} EU/mL")),
    str(QT_TRANSLATE_NOOP("Conclusion", "第 {series} 系列（{tubes} 号管）：{readings}")),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion",
            "第 {series} 系列（{tubes} 号管）：{readings}，终点 {endpoint} EU/mL（{multiple}λ）",
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "阴性对照（{tubes} 号管）：全部阴性")),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "λc = antilg(mean(lg 终点)) = antilg(({terms}) / {count}) ≈ {value}"
        )
    ),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "λc = {value} EU/mL，判定范围 0.5λ–2λ（含）= {low}–{high} EU/mL"
        )
    ),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "{role}（{tubes} 号管）：阳性 {positive}，阴性 {negative}，待定 {other}"
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "模板布局待确认：没有安排试管")),
    str(QT_TRANSLATE_NOOP("Conclusion", "模板布局待确认：模板标记为推定布局，等待 QA 确认")),
    str(QT_TRANSLATE_NOOP("Conclusion", "模板布局待确认：{tubes} 号管没有标记用途")),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "判定规则待 QA 核对（中国药典通则 1143），不给出合格或不合格"
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "{tubes} 号管还没有结果")),
    str(QT_TRANSLATE_NOOP("Conclusion", "{tubes} 号管等待人工复核")),
    str(QT_TRANSLATE_NOOP("Conclusion", "没有可用的鲎试剂标示灵敏度 λ")),
    str(
        QT_TRANSLATE_NOOP("Conclusion", "管位与规则要求的布局不符：阴性对照 {tubes} 号管加了内毒素")
    ),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion",
            "管位与规则要求的布局不符：{tubes} 号管的外加内毒素不是 2λ、λ、λ/2、λ/4 之一",
        )
    ),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion",
            "管位与规则要求的布局不符：{multiple}λ 应有 {expected} 管，实际 {actual} 管",
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "模板没有标记阴性对照")),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "最大浓度 {level} EU/mL 不是全部阳性（{tubes} 号管不是阳性）"
        )
    ),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion", "最低浓度 {level} EU/mL 不是全部阴性（{tubes} 号管不是阴性）"
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "阴性对照不是阴性（{tubes} 号管）")),
    str(
        QT_TRANSLATE_NOOP(
            "Conclusion",
            "第 {series} 系列不连续（阴性之后又出现阳性），需 QA 判定；上面的 λc 只作参考",
        )
    ),
    str(QT_TRANSLATE_NOOP("Conclusion", "λc = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）")),
    str(QT_TRANSLATE_NOOP("Conclusion", "这个试验完成时还没有结论功能，没有入库的结论")),
    str(QT_TRANSLATE_NOOP("Conclusion", "试验已停止，不产生结论")),
)
"""让翻译工具收集 :data:`NOTE_TEMPLATES` 的原文；测试保证它们和领域层的原文一字不差。"""


def conclusion_verdict_label(verdict: ConclusionVerdict) -> str:
    """结论的当前语言名称。

    Args:
        verdict: 结论。

    Returns:
        例如「合格」。
    """
    return QCoreApplication.translate("Conclusion", _VERDICTS[verdict])


def conclusion_source_label(source: str) -> str:
    """规则来源的当前语言文字。

    Args:
        source: 规则来源（入库的原文）。

    Returns:
        翻译后的文字；没有译文时是原文。
    """
    return QCoreApplication.translate("Conclusion", source)


def conclusion_note_text(note: ConclusionNote) -> str:
    """一条依据或前提的当前语言文字。

    Args:
        note: 依据或前提。

    Returns:
        翻译并代入参数后的文字。
    """
    params = dict(note.params)
    if "role" in params:
        params["role"] = tube_role_label(TubeRole(params["role"]))
    source = NOTE_TEMPLATES[note.code]
    translated = QCoreApplication.translate("Conclusion", source)
    text = translated.format(**params)
    # 管号列表由领域层用顿号连接；非中文界面改成英文标点。
    return text if translated == source else text.replace("、", ", ")


def format_concentration(value: float) -> str:
    """格式化浓度：最多 4 位有效数字。

    Args:
        value: 数值。

    Returns:
        例如 ``0.5946``。
    """
    return format_quantity(value)


_PERMISSIONS: dict[Permission, str] = {
    Permission.USER_MANAGE: str(QT_TRANSLATE_NOOP("Permission", "管理用户和角色")),
    Permission.INSTRUMENT_CONFIGURE: str(QT_TRANSLATE_NOOP("Permission", "修改仪器设置")),
    Permission.AUDIT_VIEW: str(QT_TRANSLATE_NOOP("Permission", "查看审计追踪")),
    Permission.TEMPLATE_VIEW: str(QT_TRANSLATE_NOOP("Permission", "查看检测模板")),
    Permission.TEMPLATE_MANAGE: str(QT_TRANSLATE_NOOP("Permission", "新建和修改检测模板")),
    Permission.REAGENT_MANAGE: str(QT_TRANSLATE_NOOP("Permission", "录入和校验试剂批次")),
    Permission.EXPERIMENT_VIEW: str(QT_TRANSLATE_NOOP("Permission", "查看试验和结果")),
    Permission.EXPERIMENT_CREATE: str(QT_TRANSLATE_NOOP("Permission", "新建试验")),
    Permission.EXPERIMENT_RUN: str(QT_TRANSLATE_NOOP("Permission", "启动试验")),
    Permission.EXPERIMENT_STOP: str(QT_TRANSLATE_NOOP("Permission", "停止试验")),
    Permission.RESULT_REVIEW: str(QT_TRANSLATE_NOOP("Permission", "人工复核单管结果")),
    Permission.EXPERIMENT_SIGN: str(QT_TRANSLATE_NOOP("Permission", "签署试验和报告")),
    Permission.REPORT_VIEW: str(QT_TRANSLATE_NOOP("Permission", "预览和打印报告")),
    Permission.DATA_EXPORT: str(QT_TRANSLATE_NOOP("Permission", "导出报告和数据")),
}


def permission_label(permission: Permission) -> str:
    """权限的当前语言说明。

    Args:
        permission: 权限。

    Returns:
        例如「查看审计追踪」。
    """
    return QCoreApplication.translate("Permission", _PERMISSIONS[permission])


SETTING_LABELS: dict[str, str] = {
    "instrument_no": str(QT_TRANSLATE_NOOP("Settings", "仪器编号")),
    "incubation_minutes": str(QT_TRANSLATE_NOOP("Settings", "孵育时间（分钟）")),
    "frames_per_side": str(QT_TRANSLATE_NOOP("Settings", "翻转前后各取帧数")),
    "settle_s": str(QT_TRANSLATE_NOOP("Settings", "翻转后稳定时间（秒）")),
    "temperature_target_c": str(QT_TRANSLATE_NOOP("Settings", "目标温度（℃）")),
    "temperature_tolerance_c": str(QT_TRANSLATE_NOOP("Settings", "允许偏差 ±（℃）")),
    "temperature_hold_s": str(QT_TRANSLATE_NOOP("Settings", "越限告警持续时间（秒）")),
    "video_input": str(QT_TRANSLATE_NOOP("Settings", "视频输入类型")),
    "video_path": str(QT_TRANSLATE_NOOP("Settings", "视频路径")),
    "retention_days": str(QT_TRANSLATE_NOOP("Settings", "数据保留天数")),
    "copyright_holder": str(QT_TRANSLATE_NOOP("Settings", "版权方名称")),
    "encrypt_exports": str(QT_TRANSLATE_NOOP("Settings", "导出加密")),
}
"""设置项的显示名（带单位），设置页的标签和校验提示共用。"""

_PROBLEM_TEXTS: dict[SettingsProblemCode, str] = {
    SettingsProblemCode.REQUIRED: str(QT_TRANSLATE_NOOP("SettingsProblem", "{name}不能为空")),
    SettingsProblemCode.NOT_A_NUMBER: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "{name}必须是数字，范围 {min:g}–{max:g}")
    ),
    SettingsProblemCode.NOT_AN_INTEGER: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "{name}必须是整数，范围 {min:g}–{max:g}")
    ),
    SettingsProblemCode.OUT_OF_RANGE: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "{name}应在 {min:g}–{max:g} 之间")
    ),
    SettingsProblemCode.TOO_LONG: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "{name}不能超过 {max:g} 个字符")
    ),
    SettingsProblemCode.BAD_CHARACTERS: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "{name}不能含控制字符")
    ),
    SettingsProblemCode.PATH_MISSING: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "路径不存在：{path}")
    ),
    SettingsProblemCode.NOT_A_FILE: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "这不是一个文件：{path}")
    ),
    SettingsProblemCode.NOT_A_FOLDER: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "这不是一个文件夹：{path}")
    ),
    SettingsProblemCode.NO_IMAGES: str(
        QT_TRANSLATE_NOOP("SettingsProblem", "文件夹里没有 JPG 或 PNG 图片：{path}")
    ),
}


def setting_label(name: str) -> str:
    """设置项的当前语言显示名。

    Args:
        name: 设置项名称。

    Returns:
        带单位的名称，例如「孵育时间（分钟）」。
    """
    return QCoreApplication.translate("Settings", SETTING_LABELS[name])


def describe_settings_problem(problem: SettingsProblem) -> str:
    """一个不合法的设置项的当前语言提示。

    Args:
        problem: 校验发现的问题。

    Returns:
        例如「孵育时间（分钟）应在 0.1–1440 之间」。
    """
    template = QCoreApplication.translate("SettingsProblem", _PROBLEM_TEXTS[problem.code])
    params: dict[str, object] = {**problem.params, "name": setting_label(problem.name)}
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def describe_settings_error(error: SettingsError) -> list[str]:
    """设置校验失败的逐项提示。

    Args:
        error: 服务层抛出的异常。

    Returns:
        每个不合法项一行。
    """
    return [describe_settings_problem(p) for p in error.problems]


_BUILTIN_NAMES: tuple[str, ...] = (
    str(QT_TRANSLATE_NOOP("TemplateName", "灵敏度复核 (2λ:1)")),
    str(QT_TRANSLATE_NOOP("TemplateName", "灵敏度复核")),
    str(QT_TRANSLATE_NOOP("TemplateName", "干扰试验")),
    str(QT_TRANSLATE_NOOP("TemplateName", "复试试验")),
    str(QT_TRANSLATE_NOOP("TemplateName", "半定量试验")),
    str(QT_TRANSLATE_NOOP("TemplateName", "限度试验")),
    str(QT_TRANSLATE_NOOP("TemplateName", "阴性对照")),
)
"""内置模板的名称和默认样品名（入库的原文），显示时按语言翻译。"""


def builtin_name_text(name: str) -> str:
    """内置模板名称或默认样品名的当前语言文字，自定义的名称原样返回。

    Args:
        name: 入库的名称原文。

    Returns:
        内置名称返回译文，其余原样返回。
    """
    return QCoreApplication.translate("TemplateName", name) if name in _BUILTIN_NAMES else name


_ALERT_TEXTS: dict[AlertCode, str] = {
    AlertCode.DEVICE_FAULT: str(QT_TRANSLATE_NOOP("Alert", "设备故障")),
    AlertCode.RESTORE_FAILED: str(
        QT_TRANSLATE_NOOP("Alert", "翻转复位失败，该排可能仍处于翻转状态，需要人工检查")
    ),
    AlertCode.TRAY_FAILED: str(QT_TRANSLATE_NOOP("Alert", "退出滑台失败")),
    AlertCode.VIDEO_DISCONNECTED: str(QT_TRANSLATE_NOOP("Alert", "视频断流")),
    AlertCode.VIDEO_RECONNECTED: str(QT_TRANSLATE_NOOP("Alert", "视频已重连")),
    AlertCode.VIDEO_QUALITY: str(QT_TRANSLATE_NOOP("Alert", "视频质量告警")),
    AlertCode.SEGMENT_INTERRUPTED: str(QT_TRANSLATE_NOOP("Alert", "录制段异常")),
    AlertCode.READ_FAILED: str(QT_TRANSLATE_NOOP("Alert", "读数失败")),
    AlertCode.ROW_LATE: str(QT_TRANSLATE_NOOP("Alert", "读数晚于到时过久")),
    AlertCode.TEMPERATURE_EXCURSION: str(QT_TRANSLATE_NOOP("Alert", "温度超出允许范围")),
    AlertCode.TEMPERATURE_RECOVERED: str(QT_TRANSLATE_NOOP("Alert", "温度回到范围内")),
    AlertCode.ANALYSIS_TIMEOUT: str(QT_TRANSLATE_NOOP("Alert", "分析超时，继续等待结果")),
    AlertCode.ANALYSIS_FAILED: str(QT_TRANSLATE_NOOP("Alert", "算法分析失败，该排转人工复核")),
    AlertCode.ANALYSIS_CONTRACT: str(QT_TRANSLATE_NOOP("Alert", "算法结果不符合接口约定")),
    AlertCode.RESUME_REJECTED: str(QT_TRANSLATE_NOOP("Alert", "恢复请求被拒绝")),
    AlertCode.WORKER_STUCK: str(
        QT_TRANSLATE_NOOP("Alert", "算法线程未在时限内退出，它会在算法返回后自行结束")
    ),
    AlertCode.STORAGE_FAILED: str(QT_TRANSLATE_NOOP("Alert", "写入数据库失败")),
    AlertCode.STORAGE_BUSY: str(QT_TRANSLATE_NOOP("Alert", "数据库被其他操作占用，正在等待写入")),
    AlertCode.INTERNAL_ERROR: str(QT_TRANSLATE_NOOP("Alert", "程序内部错误")),
}

_DOOR_OPEN_TEXT = str(QT_TRANSLATE_NOOP("Alert", "舱门被打开，到时的排等舱门关闭后才读数"))
_DOOR_CLOSED_TEXT = str(QT_TRANSLATE_NOOP("Alert", "舱门已关闭"))


def describe_alert(code: AlertCode, message: str, row_index: int | None, *, closed: bool) -> str:
    """引擎告警的当前语言文字。

    简体中文显示引擎给出的完整说明（含具体数值）；英文按告警码显示概要，
    具体数值和原因见审计追踪。

    Args:
        code: 告警码。
        message: 引擎给出的完整说明（简体中文）。
        row_index: 相关的排，没有时为 None。
        closed: 仅对舱门告警有意义：True 表示舱门已关闭的恢复通知。

    Returns:
        用于横幅和提示的文字。
    """
    if code is AlertCode.DOOR_OPEN:
        source = _DOOR_CLOSED_TEXT if closed else _DOOR_OPEN_TEXT
    else:
        source = _ALERT_TEXTS.get(code, "")
    if not source:
        return message
    text = QCoreApplication.translate("Alert", source)
    if text == source:
        return message
    return text if row_index is None else f"Row{row_index:02d}: {text}"


_VIDEO_TEXTS: dict[VideoEventCode, str] = {
    VideoEventCode.BLACK_FRAME: str(QT_TRANSLATE_NOOP("Alert", "画面过暗（黑屏）")),
    VideoEventCode.OVEREXPOSED: str(QT_TRANSLATE_NOOP("Alert", "画面过曝")),
    VideoEventCode.FROZEN_FRAME: str(QT_TRANSLATE_NOOP("Alert", "画面长时间没有变化（卡帧）")),
    VideoEventCode.LOW_FPS: str(QT_TRANSLATE_NOOP("Alert", "视频帧率过低")),
    VideoEventCode.FRAMES_DROPPED: str(QT_TRANSLATE_NOOP("Alert", "视频源丢帧")),
    VideoEventCode.STREAM_STALLED: str(QT_TRANSLATE_NOOP("Alert", "很久没有收到新画面")),
    VideoEventCode.DISCONNECTED: str(QT_TRANSLATE_NOOP("Alert", "视频断流")),
    VideoEventCode.RECONNECTING: str(QT_TRANSLATE_NOOP("Alert", "正在重连视频")),
    VideoEventCode.RECONNECT_FAILED: str(QT_TRANSLATE_NOOP("Alert", "视频无法恢复")),
    VideoEventCode.PIN_OVERFLOW: str(QT_TRANSLATE_NOOP("Alert", "抓拍缓存溢出，抓拍帧可能丢失")),
    VideoEventCode.CONSUMER_ERROR: str(QT_TRANSLATE_NOOP("Alert", "视频处理出错")),
    VideoEventCode.CALLBACK_ERROR: str(QT_TRANSLATE_NOOP("Alert", "视频回调出错")),
    VideoEventCode.RECORDING_DROPPED: str(QT_TRANSLATE_NOOP("Alert", "录制队列已满，丢弃了录制帧")),
    VideoEventCode.SEGMENT_INTERRUPTED: str(QT_TRANSLATE_NOOP("Alert", "录制段不完整")),
    VideoEventCode.STOP_TIMEOUT: str(QT_TRANSLATE_NOOP("Alert", "视频线程没有及时停止")),
}


def describe_video_event(code: VideoEventCode, message: str) -> str:
    """视频管线告警的当前语言文字，规则同 :func:`describe_alert`。

    Args:
        code: 事件码。
        message: 管线给出的完整说明（简体中文）。

    Returns:
        用于横幅的文字。
    """
    source = _VIDEO_TEXTS.get(code, "")
    if not source:
        return message
    text = QCoreApplication.translate("Alert", source)
    return message if text == source else text
