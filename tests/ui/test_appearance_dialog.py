from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QRadioButton, QSlider

from gelinspect.services.appearance_service import AppearancePreferences, AppearanceService
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.pages.appearance_dialog import AppearanceDialog
from gelinspect.ui.theme.appearance import apply_appearance
from gelinspect.ui.theme.tokens import current_tokens


def test_palette_and_font_presets_preview_and_save_per_account(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = AppearanceService(tmp_path)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    assert dialog.font_buttons["system"].isChecked()
    assert not dialog.findChildren(QSlider)
    assert len(dialog.findChildren(QRadioButton)) == 5
    dialog.palette_buttons["blue"].setChecked(True)
    dialog.font_buttons["large"].setChecked(True)
    assert current_tokens().type.body == 16
    assert current_tokens().colors.primary == "#315F9B"
    dialog.accept()
    assert service.get("analyst") == AppearancePreferences(palette="blue", font_scale="large")
    assert service.get("other") == AppearancePreferences()
    apply_appearance(qapp, AppearancePreferences())


def test_cancel_restores_saved_preferences(qapp: QApplication, tmp_path: Path) -> None:
    service = AppearanceService(tmp_path)
    saved = AppearancePreferences(palette="blue", font_scale="small")
    service.save("analyst", saved)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    dialog.restore_defaults()
    assert current_tokens().colors.primary != "#315F9B"
    dialog.reject()
    assert current_tokens().colors.primary == "#315F9B"
    assert current_tokens().type.body == 13
    assert service.get("analyst") == saved
    apply_appearance(qapp, AppearancePreferences())


def test_save_failure_keeps_dialog_open_and_cancel_rolls_back(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AppearanceService(tmp_path)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    original_size = current_tokens().type.body
    dialog.font_buttons["large"].setChecked(True)

    def fail(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service, "save", fail)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "无法保存" in dialog.warning.text()
    assert service.get("analyst") == AppearancePreferences()
    dialog.reject()
    assert current_tokens().type.body == original_size


@pytest.mark.parametrize(("system_size", "expected"), [(8, 13), (14, 14), (36, 16)])
def test_system_size_is_bounded(system_size: int, expected: int) -> None:
    from gelinspect.ui.theme.appearance import tokens_for_preferences

    tokens = tokens_for_preferences(AppearancePreferences(), system_size=system_size)
    assert tokens.type.body == expected
    assert tokens.type.hero <= 64


@pytest.mark.parametrize("palette", ["teal", "blue"])
@pytest.mark.parametrize("font_scale", ["system", "small", "large"])
def test_each_style_and_size_keeps_semantic_result_colors(palette: str, font_scale: str) -> None:
    from gelinspect.ui.theme.appearance import tokens_for_preferences

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
    from PySide6.QtGui import QFont, QFontDatabase

    from gelinspect.ui.theme.appearance import system_body_size

    font = QFont()
    font.setPixelSize(system_size)
    monkeypatch.setattr(QFontDatabase, "systemFont", lambda _kind: font)
    assert system_body_size() == expected


@pytest.mark.parametrize("code", ["zh_CN", "zh_TW", "en"])
def test_palette_options_are_present_in_every_language(
    qapp: QApplication, tmp_path: Path, language: LanguageManager, code: str
) -> None:
    from gelinspect.ui.i18n import Language

    language.set_language(Language(code), persist=False)
    dialog = AppearanceDialog("analyst", AppearanceService(tmp_path), app=qapp)
    assert set(dialog.palette_buttons) == {"teal", "blue"}
    assert set(dialog.font_buttons) == {"system", "small", "large"}
    dialog.reject()


@pytest.mark.parametrize("code", ["zh_CN", "zh_TW", "en"])
@pytest.mark.parametrize("font_scale", ["small", "large"])
def test_appearance_dialog_fits_fixed_font_presets(
    qapp: QApplication,
    tmp_path: Path,
    language: LanguageManager,
    code: str,
    font_scale: str,
) -> None:
    from gelinspect.ui.i18n import Language
    from tests.ui.conftest import settle
    from tests.ui.test_layout_languages import overflow_report

    language.set_language(Language(code), persist=False)
    service = AppearanceService(tmp_path)
    service.save("analyst", AppearancePreferences(font_scale=font_scale))
    dialog = AppearanceDialog("analyst", service, app=qapp)
    try:
        dialog.show()
        settle(qapp)
        assert overflow_report(dialog) == []
    finally:
        dialog.reject()
        apply_appearance(qapp, AppearancePreferences())
