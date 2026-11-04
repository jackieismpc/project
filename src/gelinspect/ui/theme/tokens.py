"""设计 token：颜色、字体、字号、间距与圆角。

数值来自 ``docs/design/*.dc.html`` 的 CSS。界面代码只能通过 token 取色，
不得在组件里硬编码颜色，这样深色主题只需要补一份 :class:`Tokens` 即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass(frozen=True, slots=True)
class ColorTokens:
    """一套主题的全部颜色，值为 ``#RRGGBB``。"""

    bg: str = "#E9EDEC"
    """窗口底色。"""
    surface: str = "#FBFCFB"
    """卡片底色。"""
    surface_alt: str = "#F2F5F4"
    """卡片内的次级底色（空孔、读数块）。"""
    border: str = "#D3DBDA"
    """卡片细线。"""
    border_strong: str = "#B9C6C5"
    """按钮、输入框边线。"""
    border_soft: str = "#E6EBEA"
    """表格行分隔线。"""
    tab_line: str = "#C9D3D2"
    """标签栏底线。"""
    ink: str = "#16282C"
    """正文墨色。"""
    ink_muted: str = "#566B70"
    """次要文字。"""
    on_accent: str = "#FFFFFF"
    """实色底上的文字。"""
    primary: str = "#0E5A66"
    """主色（石油蓝）。"""
    primary_hover: str = "#0A4650"
    """主色悬停。"""
    primary_soft: str = "#DCEBEC"
    """主色淡底（徽标、已完成步骤）。"""
    primary_tint: str = "#CFE3E5"
    """读数中孔的淡底。"""
    focus: str = "#6FB2BC"
    """键盘焦点环。"""
    sidebar: str = "#12343B"
    """侧栏底色。"""
    sidebar_hover: str = "#1B4750"
    """侧栏悬停。"""
    sidebar_text: str = "#D8E6E6"
    """侧栏文字。"""
    positive: str = "#B4480F"
    """阳性。"""
    negative: str = "#2B6CB0"
    """阴性。"""
    negative_ink: str = "#1F5288"
    """阴性按钮文字。"""
    review: str = "#6D3FB5"
    """待复核。"""
    review_soft: str = "#CDB9F0"
    """待复核淡色（孔的下半填充）。"""
    review_bg: str = "#F0EAFA"
    """待复核提示底色。"""
    danger: str = "#B3261E"
    """危险。"""
    success: str = "#1E6B4A"
    """成功。"""
    success_bg: str = "#E3F1EA"
    """成功提示底色。"""
    warning: str = "#8A5A00"
    """警告。"""
    warning_bg: str = "#F6EBD0"
    """警告提示底色。"""
    danger_bg: str = "#F7DEDC"
    """危险提示底色。"""
    input_bg: str = "#FFFFFF"
    """输入框底色。"""
    video_bg: str = "#0B1618"
    """视频区底色。"""
    video_text: str = "#CFE0E1"
    """视频区文字。"""
    track: str = "#E0E6E5"
    """进度条轨道。"""
    step_idle: str = "#DDE4E3"
    """未到达的步骤胶囊。"""
    plot_band: str = "#EEF4F3"
    """曲线的范围带。"""
    plot_guide: str = "#C4D0CF"
    """曲线的参考虚线。"""
    well_idle_border: str = "#C7D2D1"
    """选择模式下空孔的边线。"""
    strip_top: str = "#E4F0F1"
    """试管条背板渐变起点。"""
    strip_bottom: str = "#B7D2D5"
    """试管条背板渐变终点。"""
    tube_edge: str = "#57767B"
    """试管轮廓。"""
    gel: str = "#23565E"
    """凝胶色（绘制时叠加 62% 不透明度）。"""
    toast_bg: str = "#16282C"
    """Toast 底色。"""


@dataclass(frozen=True, slots=True)
class TypeTokens:
    """字体与字号（单位 px）。"""

    sans: tuple[str, ...] = ("Noto Sans SC", "PingFang SC", "Microsoft YaHei", "sans-serif")
    """正文字体回退链，内置字体在前。"""
    mono: tuple[str, ...] = ("IBM Plex Mono", "Menlo", "Consolas", "monospace")
    """读数、管号、批号字体回退链。"""
    body: int = 14
    small: int = 13
    caption: int = 12
    h2: int = 15
    h1: int = 22
    readout: int = 26
    hero: int = 56
    hero_unit: int = 22
    stat: int = 22


@dataclass(frozen=True, slots=True)
class SpaceTokens:
    """间距（单位 px）。"""

    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    card_x: int = 18
    card_y: int = 16
    touch: int = 44
    """触控目标最小高度。"""


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    """圆角（单位 px）。"""

    card: int = 8
    button: int = 6
    pill: int = 999
    tile: int = 6


@dataclass(frozen=True, slots=True)
class Tokens:
    """一套完整主题。"""

    name: str = "light"
    colors: ColorTokens = field(default_factory=ColorTokens)
    type: TypeTokens = field(default_factory=TypeTokens)
    space: SpaceTokens = field(default_factory=SpaceTokens)
    radius: RadiusTokens = field(default_factory=RadiusTokens)

    def color_names(self) -> tuple[str, ...]:
        """列出全部颜色 token 名。

        Returns:
            颜色字段名元组。
        """
        return tuple(f.name for f in fields(self.colors))


LIGHT = Tokens()
"""浅色主题。"""

_THEMES: dict[str, Tokens] = {"light": LIGHT}
_current: Tokens = LIGHT


def register_theme(tokens: Tokens) -> None:
    """注册一套主题（深色主题落地时使用）。

    Args:
        tokens: 主题 token，``name`` 作为键。
    """
    _THEMES[tokens.name] = tokens


def set_theme(name: str) -> Tokens:
    """切换当前主题。

    Args:
        name: 主题名，目前只有 ``light``。

    Returns:
        切换后的 token。

    Raises:
        KeyError: 主题未注册。
    """
    global _current
    _current = _THEMES[name]
    return _current


def current_tokens() -> Tokens:
    """返回当前主题的 token。

    Returns:
        当前 :class:`Tokens`。
    """
    return _current
