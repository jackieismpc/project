"""默认测试账号：数据目录首次初始化时创建，不要求改密。已存在账号时不会覆盖。"""

from __future__ import annotations

from collections.abc import Sequence

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import PermissionGroup as G
from gelinspect.interfaces.bootstrap import AccountProvisioner, DemoAccount

DEMO_PASSWORD = "12345678"
"""三个演示账号共用的密码。"""

DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount("admin", DEMO_PASSWORD, "Alex Chen", Role.ADMIN),
    DemoAccount("experimenter", DEMO_PASSWORD, "Jordan Li", Role.USER, frozenset({G.EXPERIMENT})),
    DemoAccount(
        "analyst",
        DEMO_PASSWORD,
        "Sam Wu",
        Role.USER,
        frozenset({G.VIEW, G.EXPERIMENT, G.OUTPUT, G.AUDIT}),
    ),
)
"""三个角色各一个演示账号。"""


class DemoAccounts:
    """``BootstrapHook`` 的演示实现。"""

    def accounts(self) -> Sequence[DemoAccount]:
        """列出演示账号。

        Returns:
            三个演示账号。
        """
        return DEMO_ACCOUNTS

    def initialize(self, provisioner: AccountProvisioner) -> None:
        """创建演示账号，已存在的不覆盖。

        Args:
            provisioner: 账号创建接口。
        """
        for account in DEMO_ACCOUNTS:
            provisioner.create_user(
                username=account.username,
                display_name=account.display_name,
                role=account.role,
                permission_groups=account.permission_groups,
                password=account.password,
                must_change_password=False,
            )
