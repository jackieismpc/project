"""领域枚举在界面上的显示名称，随语言切换。

源文本是简体中文，用 ``QT_TRANSLATE_NOOP`` 标记，显示时经 ``QCoreApplication.translate``。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.enums import ExperimentType, ReagentKind, Role, TubeRole
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import AuthError, AuthErrorCode
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.reagent_service import ReagentLockedError, ReagentScan, ScanOutcome

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
    ServiceErrorCode.MEDIA_MISSING: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {row} 排没有可播放的录像")
    ),
    ServiceErrorCode.MEDIA_TAMPERED: str(
        QT_TRANSLATE_NOOP("ServiceError", "第 {row} 排的录像与入库的哈希不符，可能已被改动")
    ),
    ServiceErrorCode.REAGENT_LOCKED: str(
        QT_TRANSLATE_NOOP("ServiceError", "试剂未通过校验，流程已锁定")
    ),
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
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template
