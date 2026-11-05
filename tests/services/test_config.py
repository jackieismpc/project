"""配置读取、后端加载和应用组装。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from gelinspect.app_context import build_app_context
from gelinspect.domain.enums import Interpretation
from gelinspect.domain.state_machine import ExperimentEvent
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.infrastructure.db import open_database, verify_audit_chain
from gelinspect.interfaces.analysis import GelAnalyzer
from gelinspect.interfaces.device import DeviceController
from gelinspect.services.config import (
    DEFAULT_CONFIG,
    AppConfig,
    ConfigError,
    load_backend,
    load_config,
    parse_config,
    project_root,
)
from simulation.mock_analyzer import MockAnalyzer
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource
from tests.services.engine_helpers import OPERATOR, ready_experiment

ROOT = Path(__file__).parents[2]


def sim_config(tmp_path: Path, **engine: object) -> dict[str, object]:
    """快速的模拟配置：写到临时目录，排很快到时。"""
    return {
        "data_dir": str(tmp_path / "数据"),
        "analysis": {
            "backend": "simulation.mock_analyzer:MockAnalyzer",
            "config_path": "simulation/analyzer.example.yaml",
            "timeout_ms": 1500,
        },
        "video": {
            "source": "simulation.sim_video:SimVideoSource",
            "device": "sim",
            "width": 960,
            "height": 540,
            "fps": 40,
            "read_timeout_ms": 50,
            "snapshot": {"settle_s": 0.05},
        },
        "device": {
            "backend": "simulation.sim_device:SimDevice",
            "options": {"time_scale": 6000.0},
        },
        "engine": {
            "incubation_minutes": 60,
            "time_scale": 36000.0,
            "status_poll_s": 0.01,
            **engine,
        },
    }


def write_yaml(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_default_config_points_to_simulation() -> None:
    config = load_config()
    assert config.analysis.backend == "simulation.mock_analyzer:MockAnalyzer"
    assert config.video.source == "simulation.sim_video:SimVideoSource"
    assert config.device.backend == "simulation.sim_device:SimDevice"
    assert config.data_dir == project_root() / "data"
    assert config.database_path.parent == config.data_dir
    assert config.analysis.config_path == project_root() / "simulation/analyzer.example.yaml"
    assert config.engine.time_scale == 60.0
    assert config.engine_config().analysis_timeout_s == 2.0


def test_example_file_matches_the_default_config() -> None:
    example = yaml.safe_load((ROOT / "simulation" / "config.example.yaml").read_text("utf-8"))
    assert example == DEFAULT_CONFIG
    assert load_config(ROOT / "simulation" / "config.example.yaml") == load_config()


def test_config_file_is_not_merged_with_the_simulation_defaults(tmp_path: Path) -> None:
    """切换到真实后端时，漏写的项不能悄悄沿用模拟后端或时间加速。"""
    data = {
        "analysis": {"backend": "real.analysis:Analyzer"},
        "video": {"source": "real.video:Camera", "device": "SN1"},
        "device": {"backend": "real.device:Controller"},
    }
    config = load_config(write_yaml(tmp_path / "real.yaml", data))
    assert config.engine.time_scale == 1.0
    assert config.engine.incubation_minutes == 60.0
    assert config.analysis.config_path is None
    missing = write_yaml(tmp_path / "missing.yaml", {"analysis": data["analysis"]})
    with pytest.raises(ConfigError, match="video"):
        load_config(missing)


def test_relative_paths_do_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.data_dir == ROOT / "data"
    assert config.analysis.config_path is not None and config.analysis.config_path.is_file()
    assert project_root() == ROOT


def test_file_video_source_path_is_resolved(tmp_path: Path) -> None:
    data = sim_config(tmp_path)
    data["video"] = {"source": "file", "device": "clips/demo.mp4"}
    config = parse_config(data)
    assert config.video.device == str(project_root() / "clips/demo.mp4")
    data["video"] = {"source": "pkg.mod:Camera", "device": "SN-12"}
    assert parse_config(data).video.device == "SN-12"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unknown=1),
        lambda d: d["engine"].update(time_scale=0),
        lambda d: d["engine"].update(frames_per_side=2),
        lambda d: d["engine"].update(typo_field=1),
        lambda d: d["video"].update(fps=-1),
        lambda d: d["analysis"].update(timeout_ms=0),
        lambda d: d.update(interpretation={"not_a_state": "positive"}),
        lambda d: d.update(interpretation={"fully_gelled": "maybe"}),
        lambda d: d["analysis"].update(config_path="no/such/file.yaml"),
    ],
)
def test_bad_config_is_rejected(tmp_path: Path, mutate: object) -> None:
    data = sim_config(tmp_path)
    mutate(data)  # type: ignore[operator]
    with pytest.raises(ConfigError):
        parse_config(data)


@pytest.mark.parametrize(
    ("content", "message"),
    [("a: [", "YAML"), ("- 1\n- 2\n", "字典")],
)
def test_bad_yaml_file_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_missing_config_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="无法读取"):
        load_config(tmp_path / "none.yaml")


def test_interpretation_mapping_is_configurable(tmp_path: Path) -> None:
    data = sim_config(tmp_path)
    data["interpretation"] = {"partially_gelled": "positive"}
    mapping = parse_config(data).mapping()
    assert mapping.partially_gelled is Interpretation.POSITIVE
    assert mapping.not_gelled is Interpretation.NEGATIVE


def test_load_backend_creates_instances_from_either_format() -> None:
    device = load_backend(
        "device.backend",
        "simulation.sim_device:SimDevice",
        DeviceController,  # type: ignore[type-abstract]
        {"time_scale": 2.0},
    )
    assert isinstance(device, SimDevice) and device.time_scale == 2.0
    dotted = load_backend(
        "analysis.backend",
        "simulation.mock_analyzer.MockAnalyzer",
        GelAnalyzer,  # type: ignore[type-abstract]
        {},
    )
    assert isinstance(dotted, MockAnalyzer)


@pytest.mark.parametrize(
    ("spec", "options", "message"),
    [
        ("nodots", {}, "格式"),
        ("no.such.module:Thing", {}, "无法导入"),
        ("simulation.sim_device:NoSuchClass", {}, "没有名为"),
        ("simulation.sim_device:BASE_TEMPERATURE_C", {}, "没有名为"),
        ("simulation.sim_device:SimDevice", {"bad_option": 1}, "创建"),
        ("simulation.mock_analyzer:MockAnalyzer", {}, "没有实现"),
    ],
)
def test_load_backend_errors_are_clear(spec: str, options: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_backend(
            "device.backend",
            spec,
            DeviceController,  # type: ignore[type-abstract]
            options,
        )


def test_missing_simulation_gives_a_clear_error_and_leaves_no_files(tmp_path: Path) -> None:
    """simulation 被移走：主代码能导入，默认配置报清楚的错误，不回退，也不建数据目录。"""
    script = dedent(
        """
        import importlib.abc, sys
        from pathlib import Path

        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "simulation" or name.startswith("simulation."):
                    raise ModuleNotFoundError(f"No module named {name!r}", name=name)
                return None

        sys.meta_path.insert(0, Block())
        from gelinspect.app_context import build_app_context
        from gelinspect.services.config import ConfigError, load_config

        data_dir = Path(sys.argv[1]) / "data"
        config = load_config().model_copy(update={"data_dir": data_dir})
        try:
            build_app_context(config)
        except ConfigError as exc:
            print(exc)
            assert not data_dir.exists()
            raise SystemExit(7)
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 7, done.stdout + done.stderr
    assert "device.backend" in done.stdout
    assert "simulation" in done.stdout
    assert "真实实现" in done.stdout


def test_missing_simulation_for_video_source_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = sim_config(tmp_path)
    data["device"] = {"backend": "tests.services.test_config:RealDevice"}
    for name in list(sys.modules):
        if name == "simulation" or name.startswith("simulation."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "simulation", None)
    with pytest.raises(ConfigError, match=r"video\.source.*simulation"):
        build_app_context(parse_config(data))


class RealDevice(SimDevice):
    """代表一个不在 simulation 下的真实设备类。"""


def test_app_context_runs_an_experiment_end_to_end(tmp_path: Path, thread_baseline: None) -> None:
    states = []
    config = load_config(write_yaml(tmp_path / "config.yaml", sim_config(tmp_path)))
    context = build_app_context(config, on_event=lambda e: states.append(type(e).__name__))
    try:
        assert isinstance(context.device, SimDevice)
        assert isinstance(context.video_source, SimVideoSource)
        assert isinstance(context.analyzer, MockAnalyzer)
        assert context.recovered == []
        assert context.engine.config.analysis_timeout_s == 1.5
        assert context.config.database_path.is_file()  # 中文路径
        context.database.experiments.save(ready_experiment(range(1, 9)))
        context.engine.start("EXP-T-1", OPERATOR)
        assert context.engine.wait(60.0)
        stored = context.database.experiments.get("EXP-T-1")
        assert stored is not None
        assert stored.state in (S.COMPLETED, S.PENDING_REVIEW)
        assert len(stored.results) == 8
        media = context.database.media.list_for_experiment("EXP-T-1")
        assert len(media) == 1 + 6 + 8 * 4  # 录像、原图、每管 4 个掩码（P9）
        assert all(Path(m.path).is_relative_to(config.data_dir) for m in media)
        assert verify_audit_chain(context.database).ok
        assert "StateChanged" in states
    finally:
        context.close()
    context.close()  # 可重复调用


def test_app_context_runs_recovery_at_startup(tmp_path: Path, thread_baseline: None) -> None:
    config = parse_config(sim_config(tmp_path))
    database = open_database(config.database_path)
    experiment = ready_experiment(range(1, 9), "CRASHED")
    experiment.apply(ExperimentEvent.START)
    database.experiments.save(experiment)
    database.close()

    context = build_app_context(config)
    try:
        assert [r.experiment_id for r in context.recovered] == ["CRASHED"]
        stored = context.database.experiments.get("CRASHED")
        assert stored is not None and stored.state is S.FAULTED
    finally:
        context.close()


def test_default_backends_load_and_run_one_row(tmp_path: Path, thread_baseline: None) -> None:
    """默认配置（P7 的一键启动）真的能跑：只把数据目录和孵育时长换掉。"""
    defaults: AppConfig = load_config()
    config = defaults.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "engine": defaults.engine.model_copy(update={"incubation_minutes": 0.0}),
        }
    )
    context = build_app_context(config)
    try:
        context.database.experiments.save(ready_experiment(range(1, 9)))
        context.engine.start("EXP-T-1", OPERATOR)
        assert context.engine.wait(60.0)
        stored = context.database.experiments.get("EXP-T-1")
        assert stored is not None and stored.state in (S.COMPLETED, S.PENDING_REVIEW)
    finally:
        context.close()
