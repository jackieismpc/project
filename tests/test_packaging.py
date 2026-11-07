"""发布包相关：运行模式与路径、发布包默认配置（无模拟、无演示账号）、启动时完整性校验。"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtWidgets import QApplication

import gelinspect
from gelinspect import startup
from gelinspect.__main__ import main
from gelinspect.app_context import build_app_context
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.security.integrity import IntegrityStatus, build_manifest
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.logging_setup import shutdown_logging
from gelinspect.services import config as config_module
from gelinspect.services import runtime_paths
from gelinspect.services.config import ConfigError, load_config, parse_config
from gelinspect.services.runtime_paths import (
    BuildInfo,
    RunMode,
    default_data_dir,
    platform_data_dir,
    project_root,
)
from gelinspect.startup import EXIT_OK, check_program_integrity, record_integrity

ROOT = Path(__file__).parents[1]
RELEASE_CONFIG = ROOT / "packaging" / "config" / "release.yaml"


@pytest.fixture(autouse=True)
def restore_global_state() -> Iterator[None]:
    hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    sys.excepthook, threading.excepthook = hook, thread_hook
    shutdown_logging()


def release_config(tmp_path: Path) -> Path:
    data = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
    data["data_dir"] = str(tmp_path / "data")
    path = tmp_path / "release.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 运行模式与路径


def test_repository_mode_keeps_using_the_data_folder_in_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GELINSPECT_DATA_DIR", raising=False)
    assert runtime_paths.run_mode() is RunMode.REPOSITORY
    assert runtime_paths.build_info() is None
    assert project_root() == ROOT
    assert runtime_paths.app_root() == ROOT
    assert default_data_dir() == ROOT / "data"
    assert load_config().data_dir == ROOT / "data"  # uv run gelinspect 的行为不变
    assert runtime_paths.translations_dir() == ROOT / "translations"


def test_a_leftover_build_info_module_does_not_switch_the_repository_to_packaged_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "gelinspect._build_info", object())
    assert runtime_paths.run_mode() is RunMode.REPOSITORY  # 没被 Nuitka 编译


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        (
            "win32",
            {"LOCALAPPDATA": "C:/Users/u/AppData/Local"},
            "C:/Users/u/AppData/Local/GelInspect",
        ),
        ("win32", {"USERPROFILE": "C:/Users/u"}, "C:/Users/u/AppData/Local/GelInspect"),
        ("darwin", {"HOME": "/Users/u"}, "/Users/u/Library/Application Support/GelInspect"),
        ("linux", {"HOME": "/home/u"}, "/home/u/.local/share/gelinspect"),
        ("linux", {"HOME": "/home/u", "XDG_DATA_HOME": "/x"}, "/x/gelinspect"),
    ],
)
def test_platform_data_directories(platform: str, environ: dict[str, str], expected: str) -> None:
    assert platform_data_dir(platform, environ) == Path(expected)


def test_packaged_mode_uses_the_platform_directory_and_can_be_overridden(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path)}
    packaged = default_data_dir(RunMode.PACKAGED, environ)
    assert packaged == platform_data_dir(environ=environ)
    assert ROOT not in packaged.parents
    override = default_data_dir(
        RunMode.PACKAGED, {**environ, "GELINSPECT_DATA_DIR": str(tmp_path / "d")}
    )
    assert override == (tmp_path / "d").resolve()


def fake_packaged(monkeypatch: pytest.MonkeyPatch, app: Path, data: Path) -> None:
    info = BuildInfo("0.1.0", "test", "release", "00" * 32)
    monkeypatch.setattr(runtime_paths, "build_info", lambda: info)
    monkeypatch.setattr(config_module, "run_mode", lambda: RunMode.PACKAGED)
    monkeypatch.setattr(config_module, "app_root", lambda mode=None: app)
    monkeypatch.setattr(config_module, "default_data_dir", lambda: data)


def test_packaged_mode_reads_the_bundled_config_and_prefers_the_deployers_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, data = tmp_path / "app", tmp_path / "AppData" / "GelInspect"
    (app / "config").mkdir(parents=True)
    (app / "config" / "gelinspect.yaml").write_text(RELEASE_CONFIG.read_text(encoding="utf-8"))
    fake_packaged(monkeypatch, app, data)
    bundled = load_config()
    assert bundled.data_dir == data  # 没写 data_dir：平台标准位置
    assert bundled.bootstrap.hook is None and "simulation" not in bundled.device.backend
    data.mkdir(parents=True)
    custom = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
    custom["instrument_no"] = "GI-DEPLOYED"
    (data / "config.yaml").write_text(yaml.safe_dump(custom), encoding="utf-8")
    assert load_config().instrument_no == "GI-DEPLOYED"


def test_relative_data_dir_still_resolves_against_the_base_directory(tmp_path: Path) -> None:
    data = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
    data["data_dir"] = "mydata"
    assert parse_config(data, base_dir=tmp_path).data_dir == tmp_path / "mydata"


# ---------------------------------------------------------------- 发布包默认配置


def test_release_config_has_no_simulation_and_no_demo_accounts(tmp_path: Path) -> None:
    text = RELEASE_CONFIG.read_text(encoding="utf-8")
    assert "simulation" not in yaml.safe_load(text).__repr__()
    config = load_config(release_config(tmp_path))
    assert config.bootstrap.hook is None
    assert config.security.key_protection == "auto" and config.security.integrity_check
    assert not config.exports.encrypt and not config.license.enabled
    context = build_app_context(config)
    try:
        assert context.demo_accounts == ()
        assert context.database.users.list_all() == []  # 首次启动：创建管理员
        status = context.device.read_status()
        assert not status.connected
        assert context.license is not None and not context.license.enabled
    finally:
        context.close()


def test_release_config_starts_into_create_admin(qapp: QApplication, tmp_path: Path) -> None:
    code = startup.run_gui(config_path=release_config(tmp_path), smoke_test=True, app=qapp)
    assert code == EXIT_OK  # 冒烟检查要求显示“创建管理员”页


def test_unconfigured_backends_fail_clearly_instead_of_faking_results() -> None:
    from gelinspect.infrastructure.unconfigured import (
        UnconfiguredAnalyzer,
        UnconfiguredDevice,
        UnconfiguredVideoSource,
    )
    from gelinspect.interfaces.analysis import AnalyzerError
    from gelinspect.interfaces.device import DeviceConnectionError
    from gelinspect.interfaces.video import VideoConfig, VideoOpenError

    with pytest.raises(DeviceConnectionError, match="device.backend"):
        UnconfiguredDevice().flip_row(1)
    with pytest.raises(VideoOpenError, match="相机尚未接入"):
        UnconfiguredVideoSource().open(VideoConfig(device="camera"))
    with pytest.raises(AnalyzerError, match="analysis.backend"):
        UnconfiguredAnalyzer().load(None)


# ---------------------------------------------------------------- 完整性校验


def test_integrity_check_is_skipped_and_logged_in_repository_mode(tmp_path: Path) -> None:
    config = load_config(release_config(tmp_path))
    report = check_program_integrity(config)
    assert report.status is IntegrityStatus.SKIPPED and "开发模式" in report.message


def make_release_dir(root: Path) -> str:
    (root / "gelinspect").mkdir(parents=True)
    (root / "gelinspect" / "lib.so").write_bytes(b"code")
    (root / "translations").mkdir()
    (root / "translations" / "gelinspect_en.qm").write_bytes(b"qm")
    key = Ed25519PrivateKey.generate()
    build_manifest(root, key, version="0.1.0", build_id="t", variant="release")
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )


def test_packaged_integrity_failure_is_audited_and_shown(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    public = make_release_dir(app)
    info = BuildInfo("0.1.0", "t", "release", public)
    monkeypatch.setattr(startup, "build_info", lambda: info)
    monkeypatch.setattr(startup, "app_root", lambda mode=None: app)
    config = load_config(release_config(tmp_path))
    assert check_program_integrity(config).status is IntegrityStatus.OK
    (app / "translations" / "gelinspect_en.qm").write_bytes(b"tampered")
    report = check_program_integrity(config)
    assert report.status is IntegrityStatus.MISMATCH
    context = build_app_context(config)
    try:
        assert not record_integrity(context, report)
        page = context.database.audit.query(
            AuditQuery(action=AuditAction.INTEGRITY_CHECKED), PageRequest()
        )
        assert len(page.items) == 1 and "gelinspect_en.qm" in page.items[0].event.detail
    finally:
        context.close()
    # 冒烟测试里校验不通过是失败；普通启动只提示，不挡住使用
    assert startup.run_gui(config_path=release_config(tmp_path), smoke_test=True, app=qapp) != 0
    shown: list[Any] = []
    monkeypatch.setattr(startup, "run_smoke_checks", lambda *a: None)

    def present(failure: Any, _log: Any) -> None:
        shown.append(failure)

    monkeypatch.setattr(startup, "_install_sigint", lambda app: None)
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    code = startup.run_gui(config_path=release_config(tmp_path), present_error=present, app=qapp)
    assert code == 0 and shown and "校验" in shown[0].title


def test_diagnose_reports_mode_paths_and_simulation(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--diagnose"]) == 0
    out = capsys.readouterr().out
    assert '"mode": "repository"' in out and '"simulation_importable": true' in out
    assert '"status": "skipped"' in out


def test_config_errors_in_diagnose_are_reported(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("data_dir: [", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)
    assert main(["--diagnose", "--config", str(bad)]) == 1


def test_package_resources_resolve_from_the_package_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert (Path(gelinspect.__file__).parent / "ui" / "assets" / "fonts").is_dir()
    assert runtime_paths.translations_dir().is_dir()
