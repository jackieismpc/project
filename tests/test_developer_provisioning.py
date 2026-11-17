"""开发账号只能通过独立交付工具初始化，密码由交付人员交互设置。"""

import sys
from pathlib import Path

import pytest

from gelinspect.bootstrap import Argon2Hasher
from gelinspect.domain.enums import Role
from gelinspect.infrastructure.db import open_database
from scripts.provision_developer import main


def test_developer_is_provisioned_without_default_password_and_cannot_be_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["provision_developer.py", "--data-dir", str(path)])
    passwords = iter(["Local-Developer-Pass1", "Local-Developer-Pass1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(passwords))
    main()
    db = open_database(path / "gelinspect.db")
    try:
        credentials = db.users.get_credentials("developer")
        assert credentials is not None and credentials.user.role is Role.DEVELOPER
        assert Argon2Hasher().verify(credentials.password_hash, "Local-Developer-Pass1")
        assert not credentials.must_change_password
        assert len(db.users.list_all()) == 1
    finally:
        db.close()
    passwords = iter(["Other-Developer-Pass2", "Other-Developer-Pass2"])
    with pytest.raises(SystemExit, match="已经存在"):
        main()
    db = open_database(path / "gelinspect.db")
    try:
        credentials = db.users.get_credentials("developer")
        assert credentials is not None
        assert Argon2Hasher().verify(credentials.password_hash, "Local-Developer-Pass1")
    finally:
        db.close()
    output = capsys.readouterr()
    assert "Local-Developer-Pass1" not in output.out + output.err


def test_invalid_password_does_not_create_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["provision_developer.py", "--data-dir", str(path)])
    monkeypatch.setattr("getpass.getpass", lambda prompt: "short")
    with pytest.raises(SystemExit, match="至少8位"):
        main()
    assert not path.exists()
