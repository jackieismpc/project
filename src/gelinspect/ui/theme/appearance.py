"""Per-account appearance preferences and their live application."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import QApplication

from gelinspect.services.appearance_service import AppearancePreferences
from gelinspect.ui.theme.stylesheet import apply_theme
from gelinspect.ui.theme.tokens import LIGHT, Tokens, activate_tokens

_COLOR_KEYS = ("primary", "bg", "surface", "ink")


def system_body_size() -> int:
    """Return the system UI size clamped to the supported 12–22 logical-pixel range."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    size = font.pixelSize()
    if size <= 0:
        size = round(font.pointSizeF() * 96 / 72) if font.pointSizeF() > 0 else 14
    return min(22, max(12, size))


def contrast_ratio(foreground: str, background: str) -> float:
    """Calculate WCAG relative-luminance contrast ratio for two colors."""

    def luminance(value: str) -> float:
        color = QColor(value)
        rgb = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def tokens_for_preferences(
    preferences: AppearancePreferences, *, system_size: int | None = None
) -> Tokens:
    """Create theme tokens from preferences while keeping Noto fixed."""
    system_size = system_body_size() if system_size is None else system_size
    body = min(22, max(12, preferences.font_size or system_size))
    overrides = {
        key: value for key in _COLOR_KEYS if (value := getattr(preferences, key)) is not None
    }
    colors = replace(LIGHT.colors, **overrides)
    if "primary" in overrides:
        primary = QColor(overrides["primary"])
        colors = replace(
            colors,
            primary_hover=primary.darker(125).name(),
            primary_soft=primary.lighter(178).name(),
            primary_tint=primary.lighter(205).name(),
        )
    scale = body / LIGHT.type.body
    type_tokens = replace(
        LIGHT.type,
        body=body,
        small=max(11, round(LIGHT.type.small * scale)),
        caption=max(10, round(LIGHT.type.caption * scale)),
        h2=max(12, round(LIGHT.type.h2 * scale)),
        h1=max(16, round(LIGHT.type.h1 * scale)),
        readout=max(18, round(LIGHT.type.readout * scale)),
        hero=max(32, round(LIGHT.type.hero * scale)),
        hero_unit=max(16, round(LIGHT.type.hero_unit * scale)),
        stat=max(18, round(LIGHT.type.stat * scale)),
    )
    return Tokens(
        name="appearance",
        colors=colors,
        type=type_tokens,
        space=LIGHT.space,
        radius=LIGHT.radius,
    )


def apply_appearance(
    app: QApplication,
    preferences: AppearancePreferences,
    *,
    system_size: int | None = None,
) -> Tokens:
    """Activate and apply a user's appearance preferences to the application."""
    tokens = activate_tokens(tokens_for_preferences(preferences, system_size=system_size))
    apply_theme(app, tokens)
    return tokens
