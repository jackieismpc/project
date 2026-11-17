from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gelinspect.services.appearance_service import AppearancePreferences, AppearanceService
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.pages.appearance_dialog import AppearanceDialog
from gelinspect.ui.theme.appearance import apply_appearance
from gelinspect.ui.theme.tokens import current_tokens


def test_font_size_previews_and_saves_per_account(qapp: QApplication, tmp_path: Path) -> None:
    service = AppearanceService(tmp_path)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    assert dialog.system_font.isChecked()
    dialog.system_font.setChecked(False)
    dialog.font_slider.setValue(18)
    assert current_tokens().type.body == 18
    dialog.accept()
    assert service.get("analyst").font_size == 18
    assert service.get("other").font_size is None
    apply_appearance(qapp, AppearancePreferences())


def test_cancel_restores_saved_preferences(qapp: QApplication, tmp_path: Path) -> None:
    service = AppearanceService(tmp_path)
    saved = AppearancePreferences(primary="#224466", font_size=16)
    service.save("analyst", saved)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    dialog.restore_defaults()
    assert current_tokens().colors.primary != saved.primary
    dialog.reject()
    assert current_tokens().colors.primary == saved.primary
    assert service.get("analyst") == saved
    apply_appearance(qapp, AppearancePreferences())


def test_save_failure_keeps_dialog_open_and_cancel_rolls_back(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AppearanceService(tmp_path)
    dialog = AppearanceDialog("analyst", service, app=qapp)
    original_size = current_tokens().type.body
    dialog.system_font.setChecked(False)
    dialog.font_slider.setValue(22)

    def fail(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service, "save", fail)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "无法保存" in dialog.warning.text()
    assert service.get("analyst") == AppearancePreferences()
    dialog.reject()
    assert current_tokens().type.body == original_size


@pytest.mark.parametrize(("system_size", "expected"), [(8, 12), (16, 16), (36, 22)])
def test_system_size_stays_in_the_supported_range(system_size: int, expected: int) -> None:
    from gelinspect.ui.theme.appearance import tokens_for_preferences

    tokens = tokens_for_preferences(AppearancePreferences(), system_size=system_size)
    assert tokens.type.body == expected


@pytest.mark.parametrize(("system_size", "expected"), [(8, 12), (16, 16), (36, 22)])
def test_detected_system_font_is_bounded(
    system_size: int, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QFont, QFontDatabase

    from gelinspect.ui.theme.appearance import system_body_size

    font = QFont()
    font.setPixelSize(system_size)
    monkeypatch.setattr(QFontDatabase, "systemFont", lambda _kind: font)
    assert system_body_size() == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("en", {"Primary color", "Background", "Content area", "Text color"}),
        ("zh_TW", {"主色", "背景", "內容區", "文字"}),
    ],
)
def test_color_field_labels_are_translated(
    qapp: QApplication, tmp_path: Path, language: LanguageManager, code: str, expected: set[str]
) -> None:
    from PySide6.QtWidgets import QLabel

    from gelinspect.ui.i18n import Language

    language.set_language(Language(code), persist=False)
    dialog = AppearanceDialog("analyst", AppearanceService(tmp_path), app=qapp)
    texts = {label.text() for label in dialog.findChildren(QLabel)}
    assert expected <= texts
    dialog.reject()


@pytest.mark.parametrize("code", ["zh_CN", "zh_TW", "en"])
@pytest.mark.parametrize("size", [12, 22])
def test_appearance_dialog_fits_at_font_limits(
    qapp: QApplication, tmp_path: Path, language: LanguageManager, code: str, size: int
) -> None:
    from gelinspect.ui.i18n import Language
    from tests.ui.conftest import settle
    from tests.ui.test_layout_languages import overflow_report

    language.set_language(Language(code), persist=False)
    service = AppearanceService(tmp_path)
    service.save("analyst", AppearancePreferences(font_size=size))
    dialog = AppearanceDialog("analyst", service, app=qapp)
    try:
        dialog.show()
        settle(qapp)
        assert overflow_report(dialog) == []
    finally:
        dialog.reject()
        apply_appearance(qapp, AppearancePreferences())
