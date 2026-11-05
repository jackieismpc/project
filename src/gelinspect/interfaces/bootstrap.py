"""首次启动的初始化钩子接口。

演示（模拟）配置通过 ``bootstrap.hook`` 引用一个实现了 :class:`BootstrapHook` 的类，
由它在数据库里创建演示账号。主代码只持有类路径字符串，不导入 ``simulation/``；
真实配置不写这一项，因此不会有任何默认账号和默认密码。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gelinspect.domain.enums import Role


@dataclass(frozen=True, slots=True)
class DemoAccount:
    """一个演示账号。

    Attributes:
        username: 登录名。
        password: 明文密码，仅用于演示配置，会显示在登录页的提示里。
        display_name: 显示名称。
        role: 角色。
    """

    username: str
    password: str
    display_name: str
    role: Role


class AccountProvisioner(Protocol):
    """初始化钩子用来创建账号的接口，由组装根用数据库实现。"""

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: Role,
        password: str,
        must_change_password: bool = False,
    ) -> bool:
        """创建账号。

        Args:
            username: 登录名。
            display_name: 显示名称。
            role: 角色。
            password: 明文密码，由实现负责哈希。
            must_change_password: 首次登录是否必须改密。

        Returns:
            新建时为 True；同名账号已存在时不覆盖，返回 False。
        """
        ...


@runtime_checkable
class BootstrapHook(Protocol):
    """首次启动初始化钩子。"""

    def accounts(self) -> Sequence[DemoAccount]:
        """列出演示账号，用于登录页的「演示账号」提示。

        Returns:
            演示账号，创建与否不影响返回内容。
        """
        ...

    def initialize(self, provisioner: AccountProvisioner) -> None:
        """首次启动（用户表为空）时调用，创建账号。必须可重复调用。

        Args:
            provisioner: 账号创建接口。
        """
        ...
