"""用 AST 检查分层依赖，防止依赖方向在后续阶段被破坏。"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "gelinspect"


def imported_roots(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def modules_in(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def is_allowed(module: str, allowed_prefixes: tuple[str, ...]) -> bool:
    root = module.split(".")[0]
    if root in sys.stdlib_module_names:
        return True
    return any(module == p or module.startswith(f"{p}.") for p in allowed_prefixes)


@pytest.mark.parametrize(
    ("layer", "allowed"),
    [
        ("domain", ("gelinspect.domain",)),
        ("interfaces", ("gelinspect.domain", "gelinspect.interfaces", "numpy", "pydantic")),
    ],
)
def test_layer_only_uses_allowed_imports(layer: str, allowed: tuple[str, ...]) -> None:
    modules = modules_in(layer)
    assert modules
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in modules
        for module in imported_roots(path)
        if not is_allowed(module, allowed)
    ]
    assert violations == []


def test_main_code_never_imports_simulation() -> None:
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in SRC.rglob("*.py")
        for module in imported_roots(path)
        if module == "simulation" or module.startswith("simulation.")
    ]
    assert violations == []


def test_infrastructure_templates_do_not_import_heavy_drivers() -> None:
    forbidden = {"ultralytics", "onnxruntime", "cv2", "PySide6"}
    templates = [
        SRC / "infrastructure" / "analysis" / "adapter_template.py",
        SRC / "infrastructure" / "device" / "adapter_template.py",
        SRC / "infrastructure" / "video" / "industrial_template.py",
    ]
    for path in templates:
        assert not {module.split(".")[0] for module in imported_roots(path)} & forbidden


def test_experiment_engine_does_not_depend_on_qt_or_infrastructure() -> None:
    modules = [
        SRC / "services" / name
        for name in (
            "experiment_engine.py",
            "engine_recovery.py",
            "engine_run.py",
            "engine_types.py",
            "engine_workers.py",
            "config.py",
        )
    ]
    violations = [
        f"{path.name}: {module}"
        for path in modules
        for module in imported_roots(path)
        if module.split(".")[0] == "PySide6" or module.startswith("gelinspect.infrastructure")
    ]
    assert violations == []


UI_ALLOWED = (
    "gelinspect.ui",
    "gelinspect.services",
    "gelinspect.domain",
    "gelinspect.interfaces",
    "PySide6",
    "pyqtgraph",
    "numpy",
)
"""界面只依赖服务、领域、接口和 Qt；不直接碰数据库、视频、设备实现或组装根。"""


def test_ui_depends_only_on_services_and_never_on_infrastructure() -> None:
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in modules_in("ui")
        for module in imported_roots(path)
        if module != "gelinspect" and not is_allowed(module, UI_ALLOWED)  # 包本身只用于取版本号
    ]
    assert violations == []


def test_ui_does_not_touch_engine_threads_or_databases() -> None:
    forbidden = ("sqlalchemy", "sqlite3", "cv2", "threading", "concurrent", "multiprocessing")
    allowed = {"bridge.py": {"threading"}}  # 桥接器只取线程号，不创建线程
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in modules_in("ui")
        for module in imported_roots(path)
        if module.split(".")[0] in forbidden and module not in allowed.get(path.name, set())
    ]
    assert violations == []


def test_new_services_do_not_depend_on_qt_or_infrastructure() -> None:
    modules = [
        SRC / "services" / name
        for name in (
            "auth.py",
            "queries.py",
            "instrument_monitor.py",
            "app_services.py",
            "errors.py",
            "template_service.py",
            "reagent_service.py",
            "experiment_draft.py",
            "experiment_service.py",
            "mask_archive.py",
            "media_service.py",
            "monitor_service.py",
            "review_service.py",
            "dashboard_service.py",
            "replay.py",
        )
    ]
    violations = [
        f"{path.name}: {module}"
        for path in modules
        for module in imported_roots(path)
        if module.split(".")[0] == "PySide6" or module.startswith("gelinspect.infrastructure")
    ]
    assert violations == []


def test_only_the_composition_root_knows_infrastructure_and_ui_together() -> None:
    """组装根（包根下的模块）之外，没有任何层同时依赖 ui 与 infrastructure。"""
    for layer in ("services", "ui"):
        for path in modules_in(layer):
            roots = set(imported_roots(path))
            assert not any(m.startswith("gelinspect.app_context") for m in roots), path
            assert not any(m.startswith("gelinspect.startup") for m in roots), path
            assert not any(m.startswith("gelinspect.bootstrap") for m in roots), path
