"""固定账号能力及六组普通用户授权。

PERMISSION_GROUPS 是业务组定义，所有界面和服务统一使用 permissions_of 计算有效权限。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from gelinspect.domain.enums import PermissionGroup as PermissionGroup
from gelinspect.domain.enums import Role


class Permission(StrEnum):
    """系统权限。"""

    COMPANY_CONFIGURE = "company_configure"
    SETTINGS_VIEW = "settings_view"
    REAGENT_VIEW = "reagent_view"
    REPORT_PRINT = "report_print"
    AUDIT_EXPORT = "audit_export"
    DIAGNOSTICS_EXPORT = "diagnostics_export"
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


_VIEW = frozenset(
    {
        Permission.EXPERIMENT_VIEW,
        Permission.TEMPLATE_VIEW,
        Permission.REPORT_VIEW,
        Permission.REAGENT_VIEW,
    }
)
PERMISSION_GROUPS: Mapping[PermissionGroup, frozenset[Permission]] = MappingProxyType(
    {
        PermissionGroup.VIEW: _VIEW,
        PermissionGroup.EXPERIMENT: _VIEW
        | {
            Permission.REAGENT_MANAGE,
            Permission.EXPERIMENT_CREATE,
            Permission.EXPERIMENT_RUN,
            Permission.EXPERIMENT_STOP,
            Permission.RESULT_REVIEW,
            Permission.EXPERIMENT_SIGN,
        },
        PermissionGroup.TEMPLATE: frozenset({Permission.TEMPLATE_VIEW, Permission.TEMPLATE_MANAGE}),
        PermissionGroup.OUTPUT: frozenset(
            {
                Permission.EXPERIMENT_VIEW,
                Permission.REPORT_VIEW,
                Permission.REPORT_PRINT,
                Permission.DATA_EXPORT,
            }
        ),
        PermissionGroup.AUDIT: frozenset({Permission.AUDIT_VIEW, Permission.AUDIT_EXPORT}),
        PermissionGroup.MAINTENANCE: frozenset(
            {
                Permission.SETTINGS_VIEW,
                Permission.INSTRUMENT_CONFIGURE,
                Permission.DIAGNOSTICS_EXPORT,
            }
        ),
    }
)


def permissions_of(
    role: Role, groups: frozenset[PermissionGroup] = frozenset()
) -> frozenset[Permission]:
    """按账号类别及授权组计算能力，普通用户默认无权限。"""
    if role is Role.DEVELOPER:
        return frozenset(Permission)
    if role is Role.ADMIN:
        return frozenset(Permission) - {Permission.COMPANY_CONFIGURE}
    if role is not Role.USER:
        return frozenset()
    return frozenset(p for g in groups for p in PERMISSION_GROUPS[g])


def has_permission(
    role: Role, permission: Permission, groups: frozenset[PermissionGroup] = frozenset()
) -> bool:
    """检查有效权限，不根据用户名或旧岗位名隐式授权。"""
    return permission in permissions_of(role, groups)


PERMISSION_ROLES: Mapping[Permission, frozenset[Role]] = MappingProxyType(
    {p: frozenset(r for r in Role if has_permission(r, p)) for p in Permission}
)
"""仅表示账号类别的固定能力；普通用户授权通过 PERMISSION_GROUPS 计算。"""
