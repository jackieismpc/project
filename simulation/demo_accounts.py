"""演示账号：模拟配置下首次启动时创建，密码公开，不要求改密。

仅用于演示和开发。真实配置不引用本模块（``bootstrap.hook`` 留空），
首次启动时由用户在“创建管理员”页自己设置密码。
"""

from __future__ import annotations

from collections.abc import Sequence

from gelinspect.domain.enums import Role
from gelinspect.interfaces.bootstrap import AccountProvisioner, DemoAccount

DEMO_PASSWORD = "12345678"
"""三个演示账号共用的密码。"""

DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount("admin", DEMO_PASSWORD, "Alex Chen", Role.ADMIN),
    DemoAccount("experimenter", DEMO_PASSWORD, "Jordan Li", Role.EXPERIMENTER),
    DemoAccount("analyst", DEMO_PASSWORD, "Sam Wu", Role.ANALYST),
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
                password=account.password,
                must_change_password=False,
            )
