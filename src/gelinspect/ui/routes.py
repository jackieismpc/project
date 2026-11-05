"""页面路由表：设计稿里的 12 个页面加「关于」，每页的标题和进入所需的权限。"""

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
        in_nav: 是否出现在侧栏。
    """

    key: str
    title: str
    permission: Permission | None = None
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
    ),
    Route(
        "audit",
        str(QT_TRANSLATE_NOOP("Routes", "审计追踪")),
        Permission.AUDIT_VIEW,
    ),
    Route(
        "users",
        str(QT_TRANSLATE_NOOP("Routes", "用户与权限")),
        Permission.USER_MANAGE,
    ),
    # 所有角色可以查看；修改需要 instrument_configure，由页面和服务层判断。
    Route("settings", str(QT_TRANSLATE_NOOP("Routes", "仪器设置"))),
    Route("about", str(QT_TRANSLATE_NOOP("Routes", "关于"))),
)
"""外壳里的页面。设计稿的 12 个页面是登录加上其中的 11 个，「关于」属于仪器设置和关于页。"""

ROUTES: tuple[Route, ...] = (LOGIN_ROUTE, *PAGE_ROUTES)
"""全部路由。"""

_BY_KEY = {route.key: route for route in ROUTES}

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
