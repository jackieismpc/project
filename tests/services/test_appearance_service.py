from pathlib import Path

import pytest

from gelinspect.services.appearance_service import (
    AppearancePreferences,
    AppearanceService,
    AppearanceSettingsError,
)


def test_preferences_are_persisted_per_account_and_validated(tmp_path: Path) -> None:
    service = AppearanceService(tmp_path)
    assert service.get("alice") == AppearancePreferences()
    preferences = AppearancePreferences(primary="#123456", bg="#eeeeee", font_size=18)
    service.save("alice", preferences)

    assert service.get("ALICE") == preferences
    assert service.get("bob") == AppearancePreferences()
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(primary="red"))
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(font_size=80))
