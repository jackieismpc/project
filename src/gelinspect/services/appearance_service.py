"""Validated, per-account storage for personal appearance preferences."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_COLORS = ("primary", "bg", "surface", "ink")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_VERSION = 1


@dataclass(frozen=True, slots=True)
class AppearancePreferences:
    """Validated per-account colors and optional fixed font size."""

    primary: str | None = None
    bg: str | None = None
    surface: str | None = None
    ink: str | None = None
    font_size: int | None = None


class AppearanceSettingsError(ValueError):
    """Stored or submitted personal appearance settings are invalid."""


def validate_preferences(preferences: AppearancePreferences) -> AppearancePreferences:
    """Validate color values and restrict manual font size to a readable range."""
    for name in _COLORS:
        value = getattr(preferences, name)
        if value is not None and not _COLOR.fullmatch(value):
            raise AppearanceSettingsError(f"{name} must be a #RRGGBB color")
    if preferences.font_size is not None and not 12 <= preferences.font_size <= 22:
        raise AppearanceSettingsError("font_size must be between 12 and 22 pixels")
    return preferences


class AppearanceService:
    """Read and write personal appearance JSON without requiring business permissions."""

    def __init__(self, data_dir: Path) -> None:
        """Create service rooted in the application's data directory."""
        self._path = data_dir / "appearance_settings.json"

    @staticmethod
    def _account_key(username: str) -> str:
        return username.strip().casefold()

    def get(self, username: str) -> AppearancePreferences:
        """Load one account's preferences, using defaults when no override exists."""
        data = self._read()
        raw = data.get(self._account_key(username), {})
        try:
            if not isinstance(raw, dict):
                raise AppearanceSettingsError("account preferences must be an object")
            prefs = AppearancePreferences(
                **{name: raw.get(name) for name in _COLORS},
                font_size=raw.get("font_size"),
            )
            return validate_preferences(prefs)
        except (TypeError, AppearanceSettingsError) as exc:
            raise AppearanceSettingsError(
                f"appearance settings for {username!r} are invalid: {exc}"
            ) from exc

    def save(self, username: str, preferences: AppearancePreferences) -> None:
        """Validate and atomically save one account's appearance preferences."""
        name = username.strip()
        if not name:
            raise AppearanceSettingsError("username is required")
        preferences = validate_preferences(preferences)
        data = self._read()
        data[self._account_key(name)] = asdict(preferences)
        payload = json.dumps({"version": _VERSION, "accounts": data}, indent=2, sort_keys=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".appearance-", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self._path)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppearanceSettingsError(f"cannot read appearance settings: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("version") != _VERSION:
            raise AppearanceSettingsError("appearance settings file has an unsupported format")
        accounts = payload.get("accounts")
        if not isinstance(accounts, dict):
            raise AppearanceSettingsError("appearance settings file is missing accounts")
        return accounts
