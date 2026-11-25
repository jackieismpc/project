"""发布包相关：运行模式与路径、单一发布包配置和运行时路径、启动时完整性校验。"""

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
from gelinspect.app_context import (
    _create_video_source,
    build_app_context,
)
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db import open_database
from gelinspect.infrastructure.security.integrity import IntegrityStatus, build_manifest
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.logging_setup import shutdown_logging
from gelinspect.services import config as config_module
from gelinspect.services import runtime_paths
from gelinspect.services.config import ConfigError, load_backend, load_config, parse_config
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.runtime_paths import (
    BuildInfo,
    RunMode,
    default_data_dir,
    platform_data_dir,
    project_root,
    validate_packaged_data_dir,
)
from gelinspect.services.video import VideoPipeline
from gelinspect.startup import EXIT_OK, check_program_integrity, record_integrity
from scripts.build import ensure_no_runtime_data

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
    packaged = default_data_dir(RunMode.PACKAGED, environ, platform="darwin")
    assert packaged == platform_data_dir(platform="darwin", environ=environ)
    assert ROOT not in packaged.parents
    override = default_data_dir(
        RunMode.PACKAGED,
        {**environ, "GELINSPECT_DATA_DIR": str(tmp_path / "d")},
        platform="darwin",
    )
    assert override == (tmp_path / "d").resolve()


def test_packaged_windows_defaults_to_app_data_while_macos_keeps_platform_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Programs" / "GelInspect"
    monkeypatch.setattr(runtime_paths, "app_root", lambda mode=None: app)
    environ = {"HOME": str(tmp_path), "LOCALAPPDATA": str(tmp_path / "LocalAppData")}
    assert default_data_dir(RunMode.PACKAGED, environ, platform="win32") == app / "data"
    assert default_data_dir(RunMode.PACKAGED, environ, platform="darwin") == (
        tmp_path / "Library" / "Application Support" / "GelInspect"
    )
    assert (
        default_data_dir(
            RunMode.PACKAGED,
            {**environ, "GELINSPECT_DATA_DIR": str(tmp_path / "custom-data")},
            platform="win32",
        )
        == (tmp_path / "custom-data").resolve()
    )


def test_packaged_windows_data_directory_cannot_overwrite_program_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Programs" / "GelInspect"
    monkeypatch.setattr(runtime_paths, "app_root", lambda mode=None: app)
    assert (
        validate_packaged_data_dir(app / "data", RunMode.PACKAGED, platform="win32")
        == (app / "data").resolve()
    )
    assert (
        validate_packaged_data_dir(app / "data" / "customer", RunMode.PACKAGED, platform="win32")
        == (app / "data" / "customer").resolve()
    )
    assert (
        validate_packaged_data_dir(tmp_path / "external-data", RunMode.PACKAGED, platform="win32")
        == (tmp_path / "external-data").resolve()
    )
    for unsafe in (app, app / "gelinspect", app / "translations"):
        with pytest.raises(ValueError, match="程序资源目录"):
            validate_packaged_data_dir(unsafe, RunMode.PACKAGED, platform="win32")


def test_windows_packaged_data_startup_creates_writable_default_and_explains_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Programs" / "GelInspect"
    monkeypatch.setattr(runtime_paths, "app_root", lambda mode=None: app)
    monkeypatch.setattr(runtime_paths, "run_mode", lambda: RunMode.PACKAGED)
    monkeypatch.setattr(sys, "platform", "win32")
    data_dir = startup._prepare_windows_packaged_data_dir(app / "data")
    assert data_dir == app / "data" and data_dir.is_dir() and not list(data_dir.iterdir())
    with pytest.raises(ConfigError, match="--data-dir"):
        startup._prepare_windows_packaged_data_dir(app / "gelinspect")


def test_inno_installer_lets_users_choose_user_install_dir_and_excludes_business_data() -> None:
    installer = (ROOT / "packaging" / "windows" / "gelinspect.iss").read_text(encoding="utf-8")
    assert "DefaultDirName={localappdata}\\Programs\\GelInspect" in installer
    assert "DisableDirPage=no" in installer
    assert 'Excludes: "data\\*"' in installer
    assert "UninstallDelete" not in installer


def fake_packaged(monkeypatch: pytest.MonkeyPatch, app: Path, data: Path) -> None:
    info = BuildInfo("0.1.0", "test", "00" * 32)
    monkeypatch.setattr(runtime_paths, "build_info", lambda: info)
    monkeypatch.setattr(config_module, "run_mode", lambda: RunMode.PACKAGED)
    monkeypatch.setattr(config_module, "app_root", lambda mode=None: app)
    monkeypatch.setattr(config_module, "default_data_dir", lambda: data)


def test_packaged_mode_reads_the_bundled_config_and_prefers_the_deployers_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, data = tmp_path / "app", tmp_path / "AppData" / "GelInspect"
    (app / "config").mkdir(parents=True)
    (app / "config" / "gelinspect.yaml").write_text(
        RELEASE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    simulation = app / "simulation"
    simulation.mkdir()
    (simulation / "analyzer.example.yaml").write_text(
        (ROOT / "simulation" / "analyzer.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fake_packaged(monkeypatch, app, data)
    bundled = load_config()
    assert bundled.data_dir == data  # 没写 data_dir：平台标准位置
    assert bundled.bootstrap.hook == "simulation.demo_accounts:DemoAccounts"
    assert bundled.device.backend == "simulation.sim_device:SimDevice"
    data.mkdir(parents=True)
    custom = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
    custom["instrument_no"] = "GI-DEPLOYED"
    (data / "config.yaml").write_text(yaml.safe_dump(custom), encoding="utf-8")
    assert load_config().instrument_no == "GI-DEPLOYED"


def test_relative_data_dir_still_resolves_against_the_base_directory(tmp_path: Path) -> None:
    data = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
    data["data_dir"] = "mydata"
    data["analysis"]["config_path"] = None
    assert parse_config(data, base_dir=tmp_path).data_dir == tmp_path / "mydata"


# ---------------------------------------------------------------- 发布包默认配置


def test_build_refuses_to_remove_runtime_data_from_an_existing_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "GelInspect"
    data_dir = bundle / "data"
    data_dir.mkdir(parents=True)
    database = data_dir / "gelinspect.db"
    database.write_bytes(b"customer database")

    with pytest.raises(SystemExit, match="备份并移出数据"):
        ensure_no_runtime_data(bundle)

    assert database.read_bytes() == b"customer database"


def test_build_allows_replacing_a_bundle_with_an_empty_data_directory(tmp_path: Path) -> None:
    bundle = tmp_path / "GelInspect"
    (bundle / "data").mkdir(parents=True)
    ensure_no_runtime_data(bundle)


def test_default_config_has_fixed_backends_and_initial_accounts(tmp_path: Path) -> None:
    text = RELEASE_CONFIG.read_text(encoding="utf-8")
    assert "simulation" in yaml.safe_load(text).__repr__()
    config = load_config(release_config(tmp_path))
    assert config.bootstrap.hook == "simulation.demo_accounts:DemoAccounts"
    assert config.analysis.config_path is not None
    assert (
        yaml.safe_load(config.analysis.config_path.read_text(encoding="utf-8"))["scripted"] is True
    )
    assert config.security.key_protection == "auto" and config.security.integrity_check
    assert not config.exports.encrypt and not config.license.enabled
    context = build_app_context(config)
    try:
        assert len(context.demo_accounts) == 3
        assert {user.username for user in context.database.users.list_all()} == {
            "admin",
            "experimenter",
            "analyst",
            "developer",
        }
        status = context.device.read_status()
        assert not status.connected  # 启动流程后才连接设备接口
        assert context.license is not None and not context.license.enabled
    finally:
        context.close()


def test_default_config_starts_with_initialized_accounts(
    qapp: QApplication, tmp_path: Path
) -> None:
    code = startup.run_gui(config_path=release_config(tmp_path), smoke_test=True, app=qapp)
    assert code == EXIT_OK  # 冒烟检查使用初始化账号完成登录流程


def test_context_close_attempts_all_resources_after_a_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_app_context(load_config(release_config(tmp_path)))
    calls: list[str] = []
    close_database = context.database.close

    def database_close() -> None:
        calls.append("database")
        close_database()

    def instrument_stop(_timeout: float) -> bool:
        calls.append("instrument")
        raise RuntimeError("monitor failed")

    def engine_shutdown(_timeout: float) -> bool:
        calls.append("engine")
        return True

    def pipeline_stop() -> None:
        calls.append("pipeline")

    def device_close() -> None:
        calls.append("device")

    monkeypatch.setattr(context.instrument, "stop", instrument_stop)
    monkeypatch.setattr(context.engine, "shutdown", engine_shutdown)
    monkeypatch.setattr(context.pipeline, "stop", pipeline_stop)
    monkeypatch.setattr(context.device, "close", device_close)
    monkeypatch.setattr(context.database, "close", database_close)

    with pytest.raises(RuntimeError, match="monitor failed"):
        context.close()
    assert calls == ["instrument", "engine", "pipeline", "database"]


def test_context_close_keeps_dependencies_alive_when_engine_does_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_app_context(load_config(release_config(tmp_path)))
    calls: list[str] = []
    close_database = context.database.close

    def instrument_stop(_timeout: float) -> bool:
        return True

    def engine_shutdown(_timeout: float) -> bool:
        return False

    def track_pipeline_stop() -> None:
        calls.append("pipeline")

    def track_device_close() -> None:
        calls.append("device")

    def track_database_close() -> None:
        calls.append("database")

    monkeypatch.setattr(context.instrument, "stop", instrument_stop)
    monkeypatch.setattr(context.engine, "shutdown", engine_shutdown)
    monkeypatch.setattr(context.pipeline, "stop", track_pipeline_stop)
    monkeypatch.setattr(context.device, "close", track_device_close)
    monkeypatch.setattr(context.database, "close", track_database_close)

    try:
        with pytest.raises(TimeoutError, match="试验引擎"):
            context.close()
        assert calls == []
    finally:
        close_database()


@pytest.mark.parametrize(
    "failure_stage", ["database", "keystore", "report_service", "engine_timeout"]
)
def test_context_assembly_releases_every_acquired_resource(
    failure_stage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gelinspect import app_context as context_module

    config = load_config(release_config(tmp_path))
    released: list[str] = []
    original_load_backend = load_backend
    original_source_factory = _create_video_source
    original_open_database = open_database
    original_pipeline_stop = VideoPipeline.stop
    opened_databases: list[Any] = []

    def tracked_backend(name: str, *args: Any, **kwargs: Any) -> Any:
        backend = original_load_backend(name, *args, **kwargs)
        if name == "device.backend":
            close = backend.close

            def close_device() -> None:
                released.append("device")
                close()

            monkeypatch.setattr(backend, "close", close_device)
        return backend

    def tracked_source(settings: Any) -> Any:
        source = original_source_factory(settings)
        close = source.close

        def close_source() -> None:
            released.append("source")
            close()

        monkeypatch.setattr(source, "close", close_source)
        return source

    def tracked_open_database(path: Path) -> Any:
        if failure_stage == "database":
            raise RuntimeError("database open failed")
        database = original_open_database(path)
        opened_databases.append(database)
        close = database.close

        def close_database() -> None:
            released.append("database")
            close()

        monkeypatch.setattr(database, "close", close_database)
        return database

    def tracked_pipeline_stop(pipeline: Any) -> None:
        released.append("pipeline")
        original_pipeline_stop(pipeline)

    monkeypatch.setattr(context_module, "load_backend", tracked_backend)
    monkeypatch.setattr(context_module, "_create_video_source", tracked_source)
    monkeypatch.setattr(context_module, "open_database", tracked_open_database)
    monkeypatch.setattr(VideoPipeline, "stop", tracked_pipeline_stop)

    def fail_keystore(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("keystore failed")

    def fail_report_service(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("report service failed")

    if failure_stage == "keystore":
        monkeypatch.setattr(context_module, "LocalKeyStore", fail_keystore)
    elif failure_stage in ("report_service", "engine_timeout"):
        monkeypatch.setattr(context_module, "ReportService", fail_report_service)
        if failure_stage == "engine_timeout":

            def engine_shutdown(*_args: Any) -> bool:
                return False

            monkeypatch.setattr(ExperimentEngine, "shutdown", engine_shutdown)

    expected_error = {
        "database": "database open failed",
        "keystore": "keystore failed",
        "report_service": "report service failed",
        "engine_timeout": "report service failed",
    }[failure_stage]
    with pytest.raises(RuntimeError, match=expected_error):
        build_app_context(config)

    if failure_stage == "engine_timeout":
        assert released == []
        assert len(opened_databases) == 1
        opened_databases[0].close()  # Test cleanup; production preserves it until process exit.
    else:
        assert "device" in released and "source" in released and "pipeline" in released
        assert ("database" in released) is (failure_stage != "database")


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
    build_manifest(root, key, version="0.1.0", build_id="t")
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
    info = BuildInfo("0.1.0", "t", public)
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


def test_build_install_data_bundles_inspection_config_and_samples(tmp_path: Path) -> None:
    from scripts.build import install_data

    app_dir = tmp_path / "app"
    staging = tmp_path / "staging"
    (staging / "translations").mkdir(parents=True)
    app_dir.mkdir()
    install_data(app_dir, staging)

    inspection_config = app_dir / "config" / "config.inspection.yaml"
    assert inspection_config.is_file()
    assert (app_dir / "simulation" / "analyzer.inspection.yaml").is_file()
    assert (app_dir / "inspection").is_dir()
    loaded = load_config(inspection_config, base_dir=app_dir)
    assert loaded.analysis.backend == "simulation.inspection_replay:InspectionReplayAnalyzer"
    assert loaded.video.source == "simulation.inspection_replay:InspectionVideoSource"
