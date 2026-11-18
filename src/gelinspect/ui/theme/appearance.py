"""受限的个人外观风格及其即时应用。"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from gelinspect.services.appearance_service import AppearancePreferences
from gelinspect.ui.theme.stylesheet import apply_theme
from gelinspect.ui.theme.tokens import LIGHT, Tokens, activate_tokens

_BLUE_COLORS = replace(
    LIGHT.colors,
    bg="#E9EEF5",
    surface="#FBFCFE",
    surface_alt="#F1F4F9",
    border="#D2DCE9",
    border_strong="#B7C5D8",
    border_soft="#E3E9F1",
    tab_line="#C5D1E0",
    ink="#18283D",
    ink_muted="#566A83",
    primary="#315F9B",
    primary_hover="#244B7E",
    primary_soft="#DFE9F6",
    primary_tint="#CCDDF1",
    focus="#75A0D2",
    sidebar="#1E3452",
    sidebar_hover="#2B4569",
    sidebar_text="#DCE7F4",
    input_bg="#FFFFFF",
    track="#E0E7F0",
    step_idle="#DCE4EF",
    plot_band="#EEF3F9",
    plot_guide="#C5D2E2",
    well_idle_border="#C4D0DF",
    strip_top="#E2EBF5",
    strip_bottom="#B8CADF",
    tube_edge="#57718F",
)
_PALETTES = {"teal": LIGHT.colors, "blue": _BLUE_COLORS}
_FONT_SIZES = {"small": 13, "large": 16}


def system_body_size() -> int:
    """读取系统正文字号并限制在13到16像素，避免标题区域过度放大。"""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    size = font.pixelSize()
    if size <= 0:
        size = round(font.pointSizeF() * 96 / 72) if font.pointSizeF() > 0 else 14
    return min(16, max(13, size))


def tokens_for_preferences(
    preferences: AppearancePreferences, *, system_size: int | None = None
) -> Tokens:
    """根据两套固定完整风格和三级字号生成主题 token。"""
    colors = _PALETTES.get(preferences.palette, _PALETTES["teal"])
    body = (
        (system_body_size() if system_size is None else min(16, max(13, system_size)))
        if preferences.font_scale == "system"
        else _FONT_SIZES.get(preferences.font_scale, 13)
    )
    scale = body / LIGHT.type.body
    type_tokens = replace(
        LIGHT.type,
        body=body,
        small=max(12, round(LIGHT.type.small * scale)),
        caption=max(11, round(LIGHT.type.caption * scale)),
        h2=max(14, round(LIGHT.type.h2 * scale)),
        h1=max(20, round(LIGHT.type.h1 * scale)),
        readout=max(24, round(LIGHT.type.readout * scale)),
        hero=max(52, round(LIGHT.type.hero * scale)),
        hero_unit=max(20, round(LIGHT.type.hero_unit * scale)),
        stat=max(20, round(LIGHT.type.stat * scale)),
    )
    return Tokens(name=f"appearance-{preferences.palette}", colors=colors, type=type_tokens)


def apply_appearance(
    app: QApplication,
    preferences: AppearancePreferences,
    *,
    system_size: int | None = None,
) -> Tokens:
    """激活并应用账号外观偏好。"""
    tokens = activate_tokens(tokens_for_preferences(preferences, system_size=system_size))
    apply_theme(app, tokens)
    return tokens
