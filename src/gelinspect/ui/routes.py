"""页面路由表：设计稿里的 12 个页面加「关于」，每页的标题、所需权限和实现阶段。

还没有实现的页面显示统一的「建设中」占位，说明它对应的阶段（见 ``docs/PLAN.md``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.permissions import Permission
from gelinspect.services.auth import Session


@dataclass(frozen=True, slots=True)
class Route:
    """一个页面的路由。

    Attributes:
        key: 稳定标识，侧栏、快捷栏和代码里都用它。
        title: 页面标题（简体中文源文本，显示时经翻译）。
        permission: 进入所需的权限，``None`` 表示所有登录用户。
        phase: 占位页说明的实现阶段，已实现的页面为 ``None``。
        summary: 占位页说明本页将包含什么（源文本）。
        in_nav: 是否出现在侧栏。
    """

    key: str
    title: str
    permission: Permission | None = None
    phase: str | None = None
    summary: str = ""
    in_nav: bool = True


LOGIN_ROUTE = Route("login", str(QT_TRANSLATE_NOOP("Routes", "登录")), in_nav=False)
"""登录页：在外壳之外，登录前显示。"""

PAGE_ROUTES: tuple[Route, ...] = (
    Route("dashboard", str(QT_TRANSLATE_NOOP("Routes", "工作台"))),
    Route("new", str(QT_TRANSLATE_NOOP("Routes", "新试验")), Permission.EXPERIMENT_CREATE),
    Route(
        "monitor",
        str(QT_TRANSLATE_NOOP("Routes", "试验监控")),
        Permission.EXPERIMENT_VIEW,
    ),
    Route(
        "tube",
        str(QT_TRANSLATE_NOOP("Routes", "单管详情")),
        Permission.EXPERIMENT_VIEW,
        in_nav=False,
    ),
    Route(
        "history",
        str(QT_TRANSLATE_NOOP("Routes", "历史记录")),
        Permission.EXPERIMENT_VIEW,
    ),
    Route("templates", str(QT_TRANSLATE_NOOP("Routes", "模板管理")), Permission.TEMPLATE_VIEW),
    Route("reagents", str(QT_TRANSLATE_NOOP("Routes", "试剂溯源")), Permission.EXPERIMENT_VIEW),
    Route(
        "reports",
        str(QT_TRANSLATE_NOOP("Routes", "报告中心")),
        Permission.REPORT_VIEW,
        "P11",
        str(QT_TRANSLATE_NOOP("Routes", "报告预览、打印、数字签名和验签。")),
    ),
    Route(
        "audit",
        str(QT_TRANSLATE_NOOP("Routes", "审计追踪")),
        Permission.AUDIT_VIEW,
        "P12",
        str(QT_TRANSLATE_NOOP("Routes", "按时间、操作者和关键字查询审计记录，并校验哈希链。")),
    ),
    Route(
        "users",
        str(QT_TRANSLATE_NOOP("Routes", "用户与权限")),
        Permission.USER_MANAGE,
        "P12",
        str(QT_TRANSLATE_NOOP("Routes", "新建、停用账号，分配角色。")),
    ),
    Route(
        "settings",
        str(QT_TRANSLATE_NOOP("Routes", "仪器设置")),
        Permission.INSTRUMENT_CONFIGURE,
        "P12",
        str(QT_TRANSLATE_NOOP("Routes", "仪器编号、相机选择、分辨率和帧率。")),
    ),
    Route("about", str(QT_TRANSLATE_NOOP("Routes", "关于"))),
)
"""外壳里的页面。设计稿的 12 个页面是登录加上其中的 11 个，「关于」属于仪器设置和关于页。"""

ROUTES: tuple[Route, ...] = (LOGIN_ROUTE, *PAGE_ROUTES)
"""全部路由。"""

_BY_KEY = {route.key: route for route in ROUTES}

PHASE_NAMES: dict[str, str] = {
    "P11": str(QT_TRANSLATE_NOOP("Phases", "P11 报告")),
    "P12": str(QT_TRANSLATE_NOOP("Phases", "P12 数据管理页")),
}
"""阶段的名称（源文本）。"""

QUICK_ACTION_ROUTES: dict[str, str] = {
    "new_experiment": "new",
    "open_file": "history",
    "templates": "templates",
    "print_report": "reports",
    "audit_trail": "audit",
    "instrument_settings": "settings",
}
"""快捷栏操作到页面的映射。"""


def route_for(key: str) -> Route:
    """按标识取路由。

    Args:
        key: 路由标识。

    Returns:
        路由。

    Raises:
        KeyError: 标识不存在。
    """
    return _BY_KEY[key]


def route_title(route: Route) -> str:
    """路由标题的当前语言文字。

    Args:
        route: 路由。

    Returns:
        翻译后的标题。
    """
    return QCoreApplication.translate("Routes", route.title)


def phase_name(phase: str) -> str:
    """阶段名称的当前语言文字。

    Args:
        phase: 阶段代码，如 ``P8``。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("Phases", PHASE_NAMES.get(phase, phase))


def can_enter(route: Route, session: Session | None) -> bool:
    """会话是否有权进入某页面（界面层判断，服务层另有校验）。

    Args:
        route: 路由。
        session: 当前会话。

    Returns:
        可以进入时为 True。
    """
    if session is None:
        return False
    return route.permission is None or session.can(route.permission)


def denied_nav_keys(session: Session | None) -> list[str]:
    """侧栏里当前会话无权进入的项。

    Args:
        session: 当前会话。

    Returns:
        导航项 key 列表。
    """
    return [r.key for r in PAGE_ROUTES if r.in_nav and not can_enter(r, session)]
