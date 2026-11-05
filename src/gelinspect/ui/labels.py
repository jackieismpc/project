"""领域枚举在界面上的显示名称，随语言切换。

源文本是简体中文，用 ``QT_TRANSLATE_NOOP`` 标记，显示时经 ``QCoreApplication.translate``。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.enums import ExperimentType, Role
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import AuthError, AuthErrorCode

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
