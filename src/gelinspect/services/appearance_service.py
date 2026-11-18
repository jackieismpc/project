"""按账号保存受限的外观风格偏好。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PALETTES = ("teal", "blue")
FONT_SCALES = ("system", "small", "large")
_VERSION = 1


@dataclass(frozen=True, slots=True)
class AppearancePreferences:
    """每个账号可选的完整配色与字号档位。"""

    palette: str = "teal"
    font_scale: str = "system"


class AppearanceSettingsError(ValueError):
    """已保存或提交的外观设置无效。"""


def validate_preferences(preferences: AppearancePreferences) -> AppearancePreferences:
    """只允许受支持的完整配色及字号档位。"""
    if preferences.palette not in PALETTES:
        raise AppearanceSettingsError("palette must be teal or blue")
    if preferences.font_scale not in FONT_SCALES:
        raise AppearanceSettingsError("font_scale must be system, small, or large")
    return preferences


class AppearanceService:
    """读写个人外观 JSON；旧版任意颜色偏好读取时归一为默认风格。"""

    def __init__(self, data_dir: Path) -> None:
        """以应用数据目录创建服务。"""
        self._path = data_dir / "appearance_settings.json"

    @staticmethod
    def _account_key(username: str) -> str:
        return username.strip()

    def get(self, username: str) -> AppearancePreferences:
        """读取一个账号的偏好。旧自由颜色和字号字段会安全回退为默认值。"""
        data = self._read()
        raw = data.get(self._account_key(username), {})
        if not isinstance(raw, dict):
            raise AppearanceSettingsError(f"appearance settings for {username!r} must be an object")
        # 旧版记录含颜色或 font_size 时忽略它们，不把任意值应用到当前主题。
        if any(key in raw for key in ("primary", "bg", "surface", "ink", "font_size")):
            return AppearancePreferences()
        try:
            preferences = AppearancePreferences(
                palette=raw.get("palette", "teal"),
                font_scale=raw.get("font_scale", "system"),
            )
            return validate_preferences(preferences)
        except (TypeError, AppearanceSettingsError) as exc:
            raise AppearanceSettingsError(
                f"appearance settings for {username!r} are invalid: {exc}"
            ) from exc

    def save(self, username: str, preferences: AppearancePreferences) -> None:
        """校验并原子保存一个账号的外观设置。"""
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
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AppearanceSettingsError(f"cannot read appearance settings: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("version") != _VERSION:
            raise AppearanceSettingsError("appearance settings file has an unsupported format")
        accounts = payload.get("accounts")
        if not isinstance(accounts, dict):
            raise AppearanceSettingsError("appearance settings file is missing accounts")
        return accounts
