"""权限矩阵。

:data:`PERMISSION_ROLES` 以“权限 → 角色集合”表达，是权限判断的唯一事实来源。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from gelinspect.domain.enums import Role


class Permission(StrEnum):
    """系统权限。"""

    USER_MANAGE = "user_manage"
    """管理用户和角色。"""
    INSTRUMENT_CONFIGURE = "instrument_configure"
    """修改仪器和相机设置。"""
    AUDIT_VIEW = "audit_view"
    """查看审计追踪。"""
    TEMPLATE_VIEW = "template_view"
    """查看检测模板。"""
    TEMPLATE_MANAGE = "template_manage"
    """新建、修改和停用检测模板。"""
    REAGENT_MANAGE = "reagent_manage"
    """录入和校验试剂批次。"""
    EXPERIMENT_VIEW = "experiment_view"
    """查看试验和结果。"""
    EXPERIMENT_CREATE = "experiment_create"
    """新建和编辑试验。"""
    EXPERIMENT_RUN = "experiment_run"
    """启动试验和恢复异常。"""
    EXPERIMENT_STOP = "experiment_stop"
    """停止试验。"""
    RESULT_REVIEW = "result_review"
    """人工复核单管结果。"""
    EXPERIMENT_SIGN = "experiment_sign"
    """签署试验和报告。"""
    REPORT_VIEW = "report_view"
    """预览和打印报告、验签。"""
    DATA_EXPORT = "data_export"
    """导出报告和数据。"""


_ALL_ROLES = frozenset(Role)

PERMISSION_ROLES: Mapping[Permission, frozenset[Role]] = MappingProxyType(
    {
        Permission.USER_MANAGE: frozenset({Role.ADMIN}),
        Permission.INSTRUMENT_CONFIGURE: frozenset({Role.ADMIN}),
        Permission.AUDIT_VIEW: frozenset({Role.ADMIN, Role.ANALYST}),
        Permission.TEMPLATE_VIEW: _ALL_ROLES,
        Permission.TEMPLATE_MANAGE: frozenset({Role.ADMIN}),
        Permission.REAGENT_MANAGE: frozenset({Role.EXPERIMENTER}),
        Permission.EXPERIMENT_VIEW: _ALL_ROLES,
        Permission.EXPERIMENT_CREATE: frozenset({Role.EXPERIMENTER}),
        Permission.EXPERIMENT_RUN: frozenset({Role.EXPERIMENTER}),
        Permission.EXPERIMENT_STOP: frozenset({Role.EXPERIMENTER}),
        Permission.RESULT_REVIEW: frozenset({Role.ANALYST}),
        Permission.EXPERIMENT_SIGN: frozenset({Role.ANALYST}),
        Permission.REPORT_VIEW: _ALL_ROLES,
        Permission.DATA_EXPORT: frozenset({Role.ADMIN, Role.ANALYST}),
    }
)
"""权限矩阵：每个权限对应可以使用它的角色。"""


def has_permission(role: Role, permission: Permission) -> bool:
    """判断角色是否拥有某权限。

    Args:
        role: 用户角色。
        permission: 要检查的权限。

    Returns:
        拥有时返回 True。
    """
    return role in PERMISSION_ROLES[permission]


def permissions_of(role: Role) -> frozenset[Permission]:
    """列出角色拥有的全部权限。

    Args:
        role: 用户角色。

    Returns:
        该角色的权限集合。
    """
    return frozenset(permission for permission, roles in PERMISSION_ROLES.items() if role in roles)
