import ast
import re
from pathlib import Path

from gelinspect.ui.theme.fonts import BUNDLED_FONT_FILES, FONT_DIR, load_fonts
from gelinspect.ui.theme.stylesheet import build_stylesheet
from gelinspect.ui.theme.tokens import LIGHT

UI = Path(__file__).resolve().parents[2] / "src" / "gelinspect" / "ui"


def test_stylesheet_has_no_unresolved_placeholders() -> None:
    qss = build_stylesheet(LIGHT)
    assert not re.findall(r"\$\{?\w+\}?", qss)
    assert "#0E5A66" in qss
    assert "Noto Sans SC" in qss


def test_no_hardcoded_colors_in_widgets() -> None:
    offenders = [
        p.name
        for p in (UI / "widgets").glob("*.py")
        if re.search(r"#[0-9A-Fa-f]{6}\b", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_fonts_bundled_and_loaded(qapp: object) -> None:
    for name in BUNDLED_FONT_FILES:
        assert (FONT_DIR / name).is_file(), name
    report = load_fonts(force=True)
    assert report.missing == ()
    assert report.sans_bundled
    assert report.mono_bundled


def test_ui_does_not_import_simulation_or_db() -> None:
    banned = ("simulation", "gelinspect.infrastructure", "sqlalchemy", "alembic")
    violations = []
    for path in UI.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            violations += [
                f"{path.name}: {n}"
                for n in names
                if any(n == b or n.startswith(b + ".") for b in banned)
            ]
    assert violations == []
