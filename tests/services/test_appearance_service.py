import json
from pathlib import Path
from typing import cast

import pytest

from gelinspect.services.appearance_service import (
    AppearancePreferences,
    AppearanceService,
    AppearanceSettingsError,
)


def test_preferences_are_persisted_per_account_and_restricted(tmp_path: Path) -> None:
    service = AppearanceService(tmp_path)
    assert service.get("alice") == AppearancePreferences()
    preferences = AppearancePreferences(palette="blue", font_scale="large")
    service.save("alice", preferences)

    assert service.get("alice") == preferences
    assert service.get("ALICE") == AppearancePreferences()
    service.save("ALICE", AppearancePreferences(font_scale="small"))
    assert service.get("alice") == preferences
    assert service.get("ALICE").font_scale == "small"
    assert service.get("bob") == AppearancePreferences()
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(palette="#123456"))
    with pytest.raises(AppearanceSettingsError):
        service.save("alice", AppearancePreferences(font_scale="custom"))


def test_legacy_free_colors_and_font_size_are_read_as_defaults_without_touching_other_accounts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "appearance_settings.json"
    legacy = {
        "version": 1,
        "accounts": {
            "alice": {"primary": "#123456", "font_size": 22},
            "bob": {"palette": "blue", "font_scale": "small"},
        },
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    service = AppearanceService(tmp_path)

    assert service.get("alice") == AppearancePreferences()
    assert service.get("bob") == AppearancePreferences(palette="blue", font_scale="small")
    service.save("alice", AppearancePreferences(palette="blue"))
    saved = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    accounts = cast(dict[str, object], saved["accounts"])
    legacy_accounts = cast(dict[str, object], legacy["accounts"])
    assert accounts["bob"] == legacy_accounts["bob"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("palette", "purple"),
        ("font_scale", "giant"),
        ("palette", True),
        ("palette", 123),
        ("palette", []),
        ("palette", {}),
        ("font_scale", True),
        ("font_scale", 123),
        ("font_scale", []),
        ("font_scale", {}),
    ],
)
def test_invalid_new_stored_values_are_reported(tmp_path: Path, field: str, value: object) -> None:
    (tmp_path / "appearance_settings.json").write_text(
        json.dumps({"version": 1, "accounts": {"alice": {field: value}}}),
        encoding="utf-8",
    )
    with pytest.raises(AppearanceSettingsError):
        AppearanceService(tmp_path).get("alice")


def test_invalid_utf8_is_reported_as_settings_error(tmp_path: Path) -> None:
    (tmp_path / "appearance_settings.json").write_bytes(b"\xff\xfe")
    with pytest.raises(AppearanceSettingsError):
        AppearanceService(tmp_path).get("alice")
