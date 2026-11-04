"""Lucide SVG 图标加载，按主题 token 着色。

图标文件位于 ``ui/assets/icons``（Lucide，ISC 许可，见同目录 ``LICENSE.lucide.txt``）。
SVG 使用 ``currentColor``，加载时替换为指定颜色再栅格化，并按屏幕缩放比渲染保证清晰。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from gelinspect.ui.theme.fonts import ASSETS_DIR
from gelinspect.ui.theme.tokens import current_tokens

ICON_DIR: Path = ASSETS_DIR / "icons"
"""内置图标目录。"""

_ALIASES = {"history": "clock", "alert": "triangle-alert", "audit": "text-align-start"}


def resolve_color(color: str) -> str:
    """把 token 名或十六进制色值统一成十六进制。

    Args:
        color: 颜色 token 名（如 ``primary``）或 ``#RRGGBB``。

    Returns:
        ``#RRGGBB`` 字符串。

    Raises:
        AttributeError: token 名不存在。
    """
    if color.startswith("#"):
        return color
    value = getattr(current_tokens().colors, color)
    return str(value)


def available_icons() -> tuple[str, ...]:
    """列出内置图标名。

    Returns:
        不含扩展名的图标名，升序。
    """
    return tuple(sorted(p.stem for p in ICON_DIR.glob("*.svg")))


@lru_cache(maxsize=256)
def _svg_bytes(name: str, color_hex: str) -> bytes:
    """读取 SVG 并替换 ``currentColor``。

    Args:
        name: 图标名。
        color_hex: 十六进制色值。

    Returns:
        着色后的 SVG 字节。

    Raises:
        FileNotFoundError: 图标不存在。
    """
    path = ICON_DIR / f"{_ALIASES.get(name, name)}.svg"
    return path.read_text(encoding="utf-8").replace("currentColor", color_hex).encode("utf-8")


class IconLoader:
    """图标加载器，缓存已渲染的像素图。"""

    _cache: dict[tuple[str, str, int, float], QPixmap] = {}

    @classmethod
    def pixmap(
        cls, name: str, size: int = 18, color: str = "ink", dpr: float | None = None
    ) -> QPixmap:
        """渲染图标。

        Args:
            name: 图标名，例如 ``video``。
            size: 逻辑像素边长。
            color: 颜色 token 名或十六进制色值。
            dpr: 设备像素比，默认取主屏。

        Returns:
            设置了设备像素比的像素图。

        Raises:
            FileNotFoundError: 图标不存在。
        """
        if dpr is None:
            screen = QGuiApplication.primaryScreen()
            dpr = float(screen.devicePixelRatio()) if screen else 1.0
        hex_color = resolve_color(color)
        key = (name, hex_color, size, dpr)
        cached = cls._cache.get(key)
        if cached is not None:
            return cached
        px = max(1, round(size * dpr))
        pixmap = QPixmap(px, px)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(_svg_bytes(name, hex_color)))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, px, px))
        painter.end()
        pixmap.setDevicePixelRatio(dpr)
        cls._cache[key] = pixmap
        return pixmap

    @classmethod
    def icon(cls, name: str, size: int = 18, color: str = "ink") -> QIcon:
        """生成 ``QIcon``。

        Args:
            name: 图标名。
            size: 逻辑像素边长。
            color: 颜色 token 名或十六进制色值。

        Returns:
            图标对象。
        """
        return QIcon(cls.pixmap(name, size, color))

    @classmethod
    def clear_cache(cls) -> None:
        """清空缓存，切换主题后调用。"""
        cls._cache.clear()
        _svg_bytes.cache_clear()
