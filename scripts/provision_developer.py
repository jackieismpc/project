"""开发方在交付环境受控创建开发账号，不提供默认密码，也不进入普通账号管理界面。

用法：uv run python scripts/provision_developer.py --data-dir /absolute/data/path
请先关闭软件；两次交互输入密码，密码不出现在参数、日志或文件中。
"""

from __future__ import annotations

import argparse
import getpass
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gelinspect.app_context import build_app_context
from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.services.auth import validate_username
from gelinspect.services.config import load_config


def main() -> None:
    """在指定数据目录创建唯一开发账号，重复执行拒绝覆盖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--username", default="developer")
    args = parser.parse_args()
    username = validate_username(args.username)
    password = getpass.getpass("开发账号密码（至少8位）：")
    if len(password) < 8 or password.casefold() == username.casefold():
        raise SystemExit("密码至少8位且不能与登录名相同")
    if password != getpass.getpass("再次输入密码："):
        raise SystemExit("两次密码不一致")
    config = load_config(Path(__file__).resolve().parents[1] / "packaging/config/release.yaml")
    config = config.model_copy(
        update={
            "data_dir": args.data_dir.resolve(),
            "bootstrap": config.bootstrap.model_copy(update={"hook": None}),
        }
    )
    context = build_app_context(config)
    try:
        with context.database.unit_of_work() as uow:
            if any(u.role is Role.DEVELOPER for u in uow.users.list_all()):
                raise SystemExit("开发账号已经存在，不允许重复创建或覆盖")
            user = User(uuid.uuid4().hex, username, "开发人员", Role.DEVELOPER)
            uow.users.add(
                user, password_hash=context.auth.hash_password(password), must_change_password=False
            )
            uow.audit.append(
                AuditEvent(
                    datetime.now(UTC),
                    "system",
                    None,
                    AuditAction.USER_CHANGED,
                    "受控初始化开发账号",
                )
            )
        print("开发账号已创建。请使用刚设置的密码登录。")
    finally:
        context.close()


if __name__ == "__main__":
    main()
