import pytest

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import (
    PERMISSION_ROLES,
    Permission,
    has_permission,
    permissions_of,
)

A = Role.ADMIN
X = Role.EXPERIMENTER
N = Role.ANALYST

EXPECTED = {
    Permission.USER_MANAGE: {A},
    Permission.INSTRUMENT_CONFIGURE: {A},
    Permission.AUDIT_VIEW: {A, N},
    Permission.TEMPLATE_VIEW: {A, X, N},
    Permission.TEMPLATE_MANAGE: {A},
    Permission.REAGENT_MANAGE: {X},
    Permission.EXPERIMENT_VIEW: {A, X, N},
    Permission.EXPERIMENT_CREATE: {X},
    Permission.EXPERIMENT_RUN: {X},
    Permission.EXPERIMENT_STOP: {X},
    Permission.RESULT_REVIEW: {N},
    Permission.EXPERIMENT_SIGN: {N},
    Permission.REPORT_VIEW: {A, X, N},
    Permission.DATA_EXPORT: {A, N},
}


def test_matrix_covers_every_permission() -> None:
    assert set(PERMISSION_ROLES) == set(Permission)


def test_matrix_matches_specification() -> None:
    assert {key: set(value) for key, value in PERMISSION_ROLES.items()} == EXPECTED


@pytest.mark.parametrize("permission", list(Permission))
@pytest.mark.parametrize("role", list(Role))
def test_has_permission_agrees_with_table(role: Role, permission: Permission) -> None:
    assert has_permission(role, permission) is (role in EXPECTED[permission])


def test_permissions_of_is_the_inverse_of_the_matrix() -> None:
    for role in Role:
        assert permissions_of(role) == {p for p, roles in EXPECTED.items() if role in roles}


def test_experimenter_cannot_review_or_sign_own_runs() -> None:
    assert not has_permission(X, Permission.RESULT_REVIEW)
    assert not has_permission(X, Permission.EXPERIMENT_SIGN)


def test_matrix_is_read_only() -> None:
    with pytest.raises(TypeError):
        PERMISSION_ROLES[Permission.USER_MANAGE] = frozenset()  # type: ignore[index]
