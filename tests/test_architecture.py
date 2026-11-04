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
