"""外观主题字号边界与检测结果语义色回归。"""

import pytest
from PySide6.QtGui import QFont, QFontDatabase

from gelinspect.services.appearance_service import AppearancePreferences
from gelinspect.ui.theme.appearance import system_body_size, tokens_for_preferences


@pytest.mark.parametrize(("system_size", "expected"), [(8, 13), (14, 14), (36, 16)])
def test_system_size_is_bounded(system_size: int, expected: int) -> None:
    tokens = tokens_for_preferences(AppearancePreferences(), system_size=system_size)
    assert tokens.type.body == expected
    assert tokens.type.hero <= 64


@pytest.mark.parametrize("palette", ["teal", "blue"])
@pytest.mark.parametrize("font_scale", ["system", "small", "large"])
def test_each_style_and_size_keeps_semantic_result_colors(palette: str, font_scale: str) -> None:
    tokens = tokens_for_preferences(
        AppearancePreferences(palette=palette, font_scale=font_scale), system_size=14
    )
    assert tokens.colors.positive == "#B4480F"
    assert tokens.colors.negative == "#2B6CB0"
    assert tokens.colors.review == "#6D3FB5"


@pytest.mark.parametrize(("system_size", "expected"), [(8, 13), (14, 14), (36, 16)])
def test_detected_system_font_is_bounded(
    system_size: int, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    font = QFont()
    font.setPixelSize(system_size)
    monkeypatch.setattr(QFontDatabase, "systemFont", lambda _kind: font)
    assert system_body_size() == expected
