"""字体加载与回退。

优先加载 ``ui/assets/fonts`` 中随软件内置的字体（OFL 许可）：

* Noto Sans SC：正文，常规、中等、粗体三个静态实例，已裁剪为 GB2312 汉字、Big5 一级字
  （台湾常用繁体字）、界面和报告里实际用到的字符，以及常用符号，约 9900 个码位。
  裁剪方法见 ``scripts/build_fonts.py``。
* IBM Plex Mono：读数、管号、批号。

内置字体缺失（例如开发时没有下载）时，回退到系统字体（PingFang SC、Microsoft YaHei、
Menlo、Consolas），并通过 :class:`FontReport` 告知调用方。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

from gelinspect.ui.theme.tokens import Tokens, current_tokens

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
"""内置资源根目录。"""
FONT_DIR = ASSETS_DIR / "fonts"
"""内置字体目录。"""

BUNDLED_FONT_FILES = (
    "NotoSansSC-Regular.ttf",
    "NotoSansSC-Medium.ttf",
    "NotoSansSC-Bold.ttf",
    "IBMPlexMono-Regular.ttf",
    "IBMPlexMono-Medium.ttf",
)
"""期望随软件内置的字体文件名。"""


@dataclass(frozen=True, slots=True)
class FontReport:
    """字体加载结果。

    Attributes:
        loaded: 成功加载的内置字体文件名。
        missing: 缺失或加载失败的内置字体文件名。
        sans: 实际采用的正文字体族。
        mono: 实际采用的等宽字体族。
        sans_bundled: 正文字体是否来自内置文件。
        mono_bundled: 等宽字体是否来自内置文件。
    """

    loaded: tuple[str, ...]
    missing: tuple[str, ...]
    sans: str
    mono: str
    sans_bundled: bool
    mono_bundled: bool


_report: FontReport | None = None


def _pick_family(candidates: tuple[str, ...], available: set[str], default: str) -> str:
    """在候选字体中选出系统里第一个存在的。

    Args:
        candidates: 按优先级排列的字体族名。
        available: 当前可用的字体族集合。
        default: 都不存在时返回的族名。

    Returns:
        选中的字体族名。
    """
    for name in candidates:
        if name in available:
            return name
    return default


def load_fonts(tokens: Tokens | None = None, *, force: bool = False) -> FontReport:
    """加载内置字体，并选出实际使用的字体族。

    必须在创建 ``QGuiApplication`` 之后调用。重复调用返回缓存结果。

    Args:
        tokens: 提供回退链的 token，默认取当前主题。
        force: 为 True 时忽略缓存，重新加载。

    Returns:
        字体加载报告。
    """
    global _report
    if _report is not None and not force:
        return _report
    tokens = tokens or current_tokens()
    loaded: list[str] = []
    missing: list[str] = []
    for filename in BUNDLED_FONT_FILES:
        path = FONT_DIR / filename
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded.append(filename)
        else:
            missing.append(filename)
    available = set(QFontDatabase.families())
    sans = _pick_family(tokens.type.sans, available, tokens.type.sans[-1])
    mono = _pick_family(tokens.type.mono, available, tokens.type.mono[-1])
    _report = FontReport(
        loaded=tuple(loaded),
        missing=tuple(missing),
        sans=sans,
        mono=mono,
        sans_bundled=sans == tokens.type.sans[0] and any("NotoSans" in n for n in loaded),
        mono_bundled=mono == tokens.type.mono[0] and any("IBMPlex" in n for n in loaded),
    )
    return _report


def last_report() -> FontReport | None:
    """返回最近一次的加载报告。

    Returns:
        报告；尚未加载时为 ``None``。
    """
    return _report


def mono_font(pixel_size: int, *, medium: bool = False) -> QFont:
    """构造一个等宽字体对象，用于自绘组件。

    Args:
        pixel_size: 像素字号。
        medium: 是否使用中等字重。

    Returns:
        配置好的 ``QFont``。
    """
    report = load_fonts()
    font = QFont(report.mono)
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.Weight.Medium if medium else QFont.Weight.Normal)
    return font


def sans_font(pixel_size: int, *, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """构造一个正文字体对象，用于自绘组件。

    Args:
        pixel_size: 像素字号。
        weight: 字重。

    Returns:
        配置好的 ``QFont``。
    """
    report = load_fonts()
    font = QFont(report.sans)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def apply_app_font(app: QGuiApplication, tokens: Tokens | None = None) -> FontReport:
    """加载字体并设置应用默认字体。

    Args:
        app: 应用对象。
        tokens: 主题 token，默认取当前主题。

    Returns:
        字体加载报告。
    """
    tokens = tokens or current_tokens()
    report = load_fonts(tokens)
    font = QFont(report.sans)
    font.setPixelSize(tokens.type.body)
    # 内置字体覆盖 GB2312 和台湾常用字，更生僻的字交给系统字体回退链补全。
    font.setFamilies([report.sans, *tokens.type.sans[1:-1]])
    app.setFont(font)
    return report
