"""六组授权的全集组合、默认拒绝及管理能力隔离。"""

import itertools

import pytest

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import (
    PERMISSION_GROUPS,
    PERMISSION_ROLES,
    Permission,
    PermissionGroup,
    has_permission,
    permissions_of,
)


def test_exactly_three_categories_and_six_groups() -> None:
    assert set(Role) == {Role.DEVELOPER, Role.ADMIN, Role.USER}
    assert len(PermissionGroup) == 6
    assert set(PERMISSION_GROUPS) == set(PermissionGroup)
    assert permissions_of(Role.USER) == frozenset()
    assert permissions_of(Role.DEVELOPER) == frozenset(Permission)
    assert permissions_of(Role.ADMIN) == frozenset(Permission) - {Permission.COMPANY_CONFIGURE}


@pytest.mark.parametrize("mask", range(64))
def test_all_group_combinations_are_exact_unions_and_never_grant_management(mask: int) -> None:
    groups = frozenset(g for i, g in enumerate(PermissionGroup) if mask & (1 << i))
    expected = frozenset(itertools.chain.from_iterable(PERMISSION_GROUPS[g] for g in groups))
    assert permissions_of(Role.USER, groups) == expected
    assert not expected & {Permission.USER_MANAGE, Permission.COMPANY_CONFIGURE}
    assert all(has_permission(Role.USER, p, groups) == (p in expected) for p in Permission)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_fixed_category_permissions_match_matrix(role: Role, permission: Permission) -> None:
    assert has_permission(role, permission) == (role in PERMISSION_ROLES[permission])


def test_read_only_is_read_only_and_output_cannot_export_audit() -> None:
    view = permissions_of(Role.USER, frozenset({PermissionGroup.VIEW}))
    assert view == {
        Permission.EXPERIMENT_VIEW,
        Permission.TEMPLATE_VIEW,
        Permission.REPORT_VIEW,
        Permission.REAGENT_VIEW,
    }
    assert Permission.AUDIT_EXPORT not in permissions_of(
        Role.USER, frozenset({PermissionGroup.OUTPUT})
    )
    assert Permission.AUDIT_EXPORT in permissions_of(Role.USER, frozenset({PermissionGroup.AUDIT}))


def test_experiment_group_includes_review_and_signing() -> None:
    permissions = permissions_of(Role.USER, frozenset({PermissionGroup.EXPERIMENT}))
    assert Permission.RESULT_REVIEW in permissions
    assert Permission.EXPERIMENT_SIGN in permissions


def test_group_definitions_are_immutable() -> None:
    with pytest.raises(TypeError):
        PERMISSION_GROUPS[PermissionGroup.VIEW] = frozenset()  # type: ignore[index]
