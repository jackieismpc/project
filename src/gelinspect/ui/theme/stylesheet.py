"""由 token 生成全局 QSS。

模板中的 ``$name`` 占位符全部来自 :class:`~gelinspect.ui.theme.tokens.Tokens`，
使用 :class:`string.Template` 严格替换，缺少 token 会立即抛 ``KeyError``。
"""

from __future__ import annotations

from dataclasses import asdict
from string import Template

from PySide6.QtWidgets import QApplication

from gelinspect.ui.theme.fonts import FontReport, apply_app_font
from gelinspect.ui.theme.tokens import Tokens, current_tokens

_QSS = Template(
    """
QWidget { color: $ink; font-family: "$sans"; font-size: ${body}px; }
QMainWindow, QDialog, QWidget[role="root"] { background: $bg; }
QLabel { background: transparent; }
QToolTip { background: $toast_bg; color: $on_accent; border: 0; padding: 4px 8px; }

QLabel[role="h1"] { font-size: ${h1}px; font-weight: 700; }
QLabel[role="h2"] { font-size: ${h2}px; font-weight: 700; }
QLabel[role="lab"] { font-size: ${caption}px; color: $ink_muted; }
QLabel[role="meta"] { font-size: ${small}px; color: $ink_muted; }
QLabel[role="val"] { font-size: ${h2}px; font-weight: 500; }
QLabel[role="body"] { font-size: ${body}px; }
QLabel[role="readout"] { font-size: ${readout}px; color: $primary; }
QLabel[role="stat"] { font-size: ${stat}px; }
QLabel[role="hero"] { font-size: ${hero}px; color: $primary; }
QLabel[role="hero-unit"] { font-size: ${hero_unit}px; color: $primary; }
QLabel[role="ok"] { color: $success; font-weight: 500; }
QLabel[role="warn"] { color: $warning; font-weight: 500; }
QLabel[role="danger"] { color: $danger; font-weight: 500; }
QLabel[role="brand"] { color: $on_accent; font-size: 16px; font-weight: 700; }
QLabel[role="video-meta"] { color: $video_text; font-size: ${caption}px; }
QLabel[mono="true"] { font-family: "$mono"; }

QFrame[role="card"] {
    background: $surface; border: 1px solid $border; border-radius: ${card}px;
}
QFrame[role="tile"] { background: $surface_alt; border: 0; border-radius: ${tile}px; }
QFrame[role="video"] { background: $video_bg; border: 0; border-radius: ${tile}px; }
QFrame[role="sidebar"] { background: $sidebar; border: 0; }
QFrame[role="empty"] { background: transparent; border: 0; }

QPushButton {
    min-height: ${touch}px; padding: 0 18px; border-radius: ${button}px;
    border: 1px solid $border_strong; background: $surface; color: $ink; font-weight: 500;
}
QPushButton:hover { background: $surface_alt; border-color: $ink_muted; }
QPushButton:pressed { background: $track; }
QPushButton:focus { border: 3px solid $focus; padding: 0 16px; }
QPushButton:disabled { color: $ink_muted; background: $track; border-color: $border; }
QPushButton:checked { background: $primary; border-color: $primary; color: $on_accent; font-weight: 700; }
QPushButton:checked:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[tone="positive"]:checked { background: $positive; border-color: $positive; color: $on_accent; }
QPushButton[tone="negative"]:checked { background: $negative; border-color: $negative; color: $on_accent; }
QPushButton:checked:disabled { background: $primary_soft; border: 2px solid $primary; color: $primary_hover; }
QPushButton[tone="positive"]:checked:disabled { background: $primary_soft; border: 2px solid $positive; color: $positive; }
QPushButton[tone="negative"]:checked:disabled { background: $primary_soft; border: 2px solid $negative; color: $negative_ink; }
QPushButton[variant="primary"] { background: $primary; border-color: $primary; color: $on_accent; }
QPushButton[variant="primary"]:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[variant="primary"]:pressed { background: $primary_hover; }
QPushButton[variant="primary"]:focus { border: 3px solid $focus; }
QPushButton[variant="primary"]:disabled { background: $track; color: $ink_muted; border-color: $border; }
QPushButton[variant="danger"] { border-color: $danger; color: $danger; }
QPushButton[variant="danger"]:hover { background: $danger; border-color: $danger; color: $on_accent; }
QPushButton[variant="danger"]:focus { border: 3px solid $focus; }

QPushButton[role="link"] { min-height: 32px; padding: 0 8px; border: 0; background: transparent; color: $ink_muted; font-weight: 400; }
QPushButton[role="link"]:hover { background: transparent; color: $ink; }
QPushButton[role="link"]:checked { color: $ink; font-weight: 700; }
QPushButton[role="link"]:focus { border: 0; padding: 0 8px; text-decoration: underline; }

QLabel[role="badge"] { border-radius: 13px; padding: 4px 12px; font-size: ${small}px; font-weight: 500; }
QLabel[role="badge"][tone="info"] { background: $primary_soft; color: $primary_hover; }
QLabel[role="badge"][tone="success"] { background: $success_bg; color: $success; }
QLabel[role="badge"][tone="warning"] { background: $warning_bg; color: $warning; }
QLabel[role="badge"][tone="danger"] { background: $danger_bg; color: $danger; }
QLabel[role="badge"][tone="review"] { background: $review_bg; color: $review; }
QLabel[role="badge"][tone="neutral"] { background: $step_idle; color: $ink_muted; }

QFrame[role="tabs"] { background: transparent; border: 0; border-bottom: 1px solid $tab_line; }
QTabBar { background: transparent; border: 0; }
QTabBar::tab {
    min-height: ${touch}px; padding: 0 16px; border: 0; border-bottom: 3px solid transparent;
    background: transparent; color: $ink_muted; margin-right: 4px;
}
QTabBar::tab:hover { color: $ink; }
QTabBar::tab:selected { color: $primary; border-bottom: 3px solid $primary; font-weight: 700; }
QTabBar[kbfocus="true"]::tab:selected { background: $primary_soft; border-top-left-radius: 6px; border-top-right-radius: 6px; }

QCheckBox { spacing: 8px; min-height: ${touch}px; }

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
    min-height: 36px; padding: 0 10px; border: 1px solid $border_strong;
    border-radius: ${button}px; background: $input_bg; selection-background-color: $primary_tint;
}
QTextEdit, QPlainTextEdit { padding: 8px 10px; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus { border: 2px solid $primary; }

QTableView {
    background: $surface; border: 0; outline: 0; gridline-color: $border_soft;
    selection-background-color: $primary_soft; selection-color: $ink;
}
QHeaderView { background: $surface; border: 0; }
QHeaderView::section {
    background: $surface; color: $ink_muted; font-weight: 500; font-size: ${small}px;
    padding: 8px 10px; border: 0; border-bottom: 1px solid $border; text-align: left;
}
QTableCornerButton::section { background: $surface; border: 0; }

QFrame[role="option"] {
    background: $surface; border: 2px solid $border; border-radius: ${card}px;
}
QFrame[role="option"]:hover { border-color: $border_strong; }
QFrame[role="option"][selected="true"] { background: $primary_soft; border-color: $primary; }
QFrame[role="option"]:focus { border-color: $focus; }
QFrame[role="reagent"] {
    background: $surface; border: 1px solid $border; border-radius: ${card}px;
}
QFrame[role="reagent"][state="ok"] { border-color: $success; }
QFrame[role="reagent"][state="bad"] { border-color: $danger; background: $danger_bg; }
QFrame[role="banner"] { border-radius: ${card}px; border: 1px solid $border; background: $surface_alt; }
QFrame[role="banner"][tone="danger"] { border-color: $danger; background: $danger_bg; }
QFrame[role="banner"][tone="warning"] { border-color: $warning; background: $warning_bg; }
QFrame[role="desk"] { background: $desk; border: 0; border-radius: ${card}px; }
QFrame[role="banner"][tone="success"] { border-color: $success; background: $success_bg; }
QFrame[role="banner"][tone="info"] { border-color: $primary; background: $primary_soft; }
QFrame[role="toolbar"] { background: $surface; border: 0; border-bottom: 1px solid $border; }
QFrame[role="vsep"] { background: $border; border: 0; min-width: 1px; max-width: 1px; }
QToolButton[role="quick"] {
    min-height: ${touch}px; padding: 0 12px; border: 2px solid transparent;
    border-radius: ${button}px; background: transparent; color: $ink; font-weight: 500;
}
QToolButton[role="quick"][iconOnly="true"] { padding: 0; min-width: ${touch}px; }
QToolButton[role="quick"]:hover { background: $surface_alt; }
QToolButton[role="quick"]:pressed { background: $track; }
QToolButton[role="quick"]:focus { border-color: $focus; }
QToolButton[role="quick"]:disabled { color: $ink_muted; background: transparent; }
QToolButton[role="quick"]::menu-indicator { image: none; width: 0; }
QLabel[role="status"] { font-size: ${small}px; color: $ink; }
QLabel[role="status"][tone="ok"] { color: $success; font-weight: 500; }
QLabel[role="status"][tone="bad"] { color: $danger; font-weight: 500; }

QMenu { background: $surface; border: 1px solid $border; border-radius: ${button}px; padding: 4px; }
QMenu::item { padding: 10px 18px 10px 12px; min-height: 24px; border-radius: ${tile}px; }
QMenu::item:selected { background: $primary_soft; }
QMenu::item:disabled { color: $ink_muted; background: transparent; }

QFrame[role="toast"] {
    background: $surface; border: 1px solid $border; border-left: 4px solid $primary;
    border-radius: ${card}px;
}
QFrame[role="toast"][tone="success"] { border-left-color: $success; }
QFrame[role="toast"][tone="warning"] { border-left-color: $warning; }
QFrame[role="toast"][tone="danger"] { border-left-color: $danger; }
QDialog[role="dialog"] { background: $surface; }

QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 32px; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 32px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""
)


def stylesheet_mapping(
    tokens: Tokens, sans: str | None = None, mono: str | None = None
) -> dict[str, str | int]:
    """展开 token 为模板替换表。

    Args:
        tokens: 主题 token。
        sans: 实际采用的正文字体族，默认取回退链第一项。
        mono: 实际采用的等宽字体族，默认取回退链第一项。

    Returns:
        占位符名到值的映射。
    """
    mapping: dict[str, str | int] = {}
    mapping.update(asdict(tokens.colors))
    mapping.update(
        {
            k: v
            for k, v in asdict(tokens.type).items()
            if isinstance(v, int)  # 字体回退链是元组，下面单独处理
        }
    )
    mapping.update(asdict(tokens.space))
    mapping.update(asdict(tokens.radius))
    mapping["sans"] = sans or tokens.type.sans[0]
    mapping["mono"] = mono or tokens.type.mono[0]
    return mapping


def build_stylesheet(
    tokens: Tokens | None = None, sans: str | None = None, mono: str | None = None
) -> str:
    """生成全局样式表。

    Args:
        tokens: 主题 token，默认取当前主题。
        sans: 实际采用的正文字体族。
        mono: 实际采用的等宽字体族。

    Returns:
        可交给 ``QApplication.setStyleSheet`` 的 QSS。

    Raises:
        KeyError: 模板引用了不存在的 token。
    """
    tokens = tokens or current_tokens()
    return _QSS.substitute(stylesheet_mapping(tokens, sans, mono))


def apply_theme(app: QApplication, tokens: Tokens | None = None) -> FontReport:
    """加载字体并为应用套用主题。

    Args:
        app: 应用对象。
        tokens: 主题 token，默认取当前主题。

    Returns:
        字体加载报告，调用方可据此提示字体是否内置。
    """
    tokens = tokens or current_tokens()
    report = apply_app_font(app, tokens)
    app.setStyleSheet(build_stylesheet(tokens, report.sans, report.mono))
    return report
