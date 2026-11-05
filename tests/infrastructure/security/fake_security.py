"""假的 macOS ``security`` 命令：在内存里模拟登录钥匙串，测试不碰真实钥匙串。"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from gelinspect.infrastructure.security.protectors import CommandResult


class FakeSecurity:
    """支持 ``default-keychain``、``find-generic-password -w`` 和 ``-i`` 模式的
    ``add-generic-password``。"""

    def __init__(self, *, available: bool = True, deny_write: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.available = available
        self.deny_write = deny_write
        self.calls: list[list[str]] = []
        self.stdin: list[str] = []

    def __call__(self, args: Sequence[str], stdin: str | None) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        if not self.available:
            return CommandResult(1, "", "security: SecKeychainCopyDefault: denied")
        command = argv[1]
        if command == "default-keychain":
            return CommandResult(0, '    "/Users/x/Library/Keychains/login.keychain-db"\n', "")
        if command == "find-generic-password":
            key = (argv[argv.index("-s") + 1], argv[argv.index("-a") + 1])
            if key not in self.items:
                return CommandResult(44, "", "The specified item could not be found")
            return CommandResult(0, self.items[key] + "\n", "")
        if command == "-i":
            assert stdin is not None
            self.stdin.append(stdin)
            if self.deny_write:
                return CommandResult(0, "", "denied")
            parts = shlex.split(stdin)
            assert parts[0] == "add-generic-password"
            key = (parts[parts.index("-s") + 1], parts[parts.index("-a") + 1])
            self.items[key] = parts[parts.index("-w") + 1]
            return CommandResult(0, "", "")
        return CommandResult(1, "", f"unknown {command}")
