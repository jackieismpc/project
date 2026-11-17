from pathlib import Path

from PySide6.QtWidgets import QApplication

from gelinspect.services.appearance_service import AppearancePreferences, AppearanceService
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
