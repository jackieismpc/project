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

    assert service.get("alice") == preferences
    assert service.get("ALICE") == AppearancePreferences()
    service.save("ALICE", AppearancePreferences(font_size=20))
    assert service.get("alice") == preferences
    assert service.get("ALICE").font_size == 20
    assert service.get("bob") == AppearancePreferences()
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(primary="red"))
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(font_size=80))


@pytest.mark.parametrize("value", [True, "16", 16.5, [], {}])
def test_invalid_stored_font_size_is_reported(tmp_path: Path, value: object) -> None:
    import json

    (tmp_path / "appearance_settings.json").write_text(
        json.dumps({"version": 1, "accounts": {"alice": {"font_size": value}}}),
        encoding="utf-8",
    )
    with pytest.raises(AppearanceSettingsError):
        AppearanceService(tmp_path).get("alice")


@pytest.mark.parametrize("value", [True, 123, [], {}])
def test_invalid_stored_color_is_reported(tmp_path: Path, value: object) -> None:
    import json

    (tmp_path / "appearance_settings.json").write_text(
        json.dumps({"version": 1, "accounts": {"alice": {"primary": value}}}),
        encoding="utf-8",
    )
    with pytest.raises(AppearanceSettingsError):
        AppearanceService(tmp_path).get("alice")


def test_invalid_utf8_is_reported_as_settings_error(tmp_path: Path) -> None:
    (tmp_path / "appearance_settings.json").write_bytes(b"\xff\xfe")
    with pytest.raises(AppearanceSettingsError):
        AppearanceService(tmp_path).get("alice")
