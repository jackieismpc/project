"""Personal appearance preferences with immediate preview and cancel rollback."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import cast

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gelinspect.services.appearance_service import (
    AppearancePreferences,
    AppearanceService,
    AppearanceSettingsError,
)
from gelinspect.ui.theme.appearance import (
    apply_appearance,
    contrast_ratio,
    system_body_size,
)
from gelinspect.ui.theme.tokens import LIGHT
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.label import Label

_log = logging.getLogger(__name__)

_COLOR_FIELDS = (
    ("primary", str(QT_TRANSLATE_NOOP("AppearanceDialog", "主色"))),
    ("bg", str(QT_TRANSLATE_NOOP("AppearanceDialog", "背景"))),
    ("surface", str(QT_TRANSLATE_NOOP("AppearanceDialog", "内容区"))),
    ("ink", str(QT_TRANSLATE_NOOP("AppearanceDialog", "文字"))),
)


class AppearanceDialog(QDialog):
    """Edit and preview colors and font size for a single account."""

    def __init__(
        self,
        username: str,
        service: AppearanceService,
        parent: QWidget | None = None,
        *,
        app: QApplication | None = None,
    ) -> None:
        """Create a previewing dialog for the named account.

        Args:
            username: Account name used to isolate stored preferences.
            service: Validating persistence service for appearance preferences.
            parent: Optional parent window.
            app: Application whose theme is previewed; defaults to the active app.
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("外观设置"))
        self.setMinimumWidth(440)
        self._username = username
        app_instance = app or cast(QApplication | None, QApplication.instance())
        if app_instance is None:
            raise RuntimeError("AppearanceDialog requires QApplication")
        self._app: QApplication = app_instance
        self._service = service
        self._original = self._service.get(username)
        self._preferences = self._original
        self._color_buttons: dict[str, Button] = {}
        self._labels: dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        root.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        description = Label(
            self.tr(
                "按账号保存。字号跟随系统或手动调整，均限制在12–22像素内。更改会立即预览，取消可恢复原设置。"
            ),
            "meta",
        )
        description.setWordWrap(True)
        root.addWidget(description)

        for key, source_label in _COLOR_FIELDS:
            row = QHBoxLayout()
            label = Label(self.tr(source_label), "lab")
            value = Label("", "body", mono=True)
            button = Button(self.tr("选择颜色"), ButtonVariant.SECONDARY)
            button.clicked.connect(lambda _checked=False, name=key: self._choose_color(name))
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(value)
            row.addWidget(button)
            root.addLayout(row)
            self._labels[key] = value
            self._color_buttons[key] = button

        font_row = QHBoxLayout()
        self.system_font = QCheckBox(self.tr("跟随系统字号"))
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(12, 22)
        self.font_value = Label("", "body", mono=True)
        font_row.addWidget(self.system_font)
        font_row.addWidget(self.font_slider, 1)
        font_row.addWidget(self.font_value)
        root.addLayout(font_row)
        self.system_font.toggled.connect(self._system_font_changed)
        self.font_slider.valueChanged.connect(self._font_changed)

        self.warning = Label("", "warn")
        self.warning.setWordWrap(True)
        root.addWidget(self.warning)
        self.preview = Card(self.tr("预览"))
        self.preview_title = Label(self.tr("报告中心"), "h2")
        self.preview_text = Label(self.tr("这是界面内容和正文文字的预览。"), "body")
        self.preview_text.setWordWrap(True)
        self.preview_action = Button(self.tr("主要操作"), ButtonVariant.PRIMARY)
        self.preview.body.addWidget(self.preview_title)
        self.preview.body.addWidget(self.preview_text)
        self.preview.body.addWidget(self.preview_action)
        root.addWidget(self.preview)

        action_row = QHBoxLayout()
        self.reset_button = Button(self.tr("恢复默认"), ButtonVariant.SECONDARY)
        action_row.addWidget(self.reset_button)
        action_row.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("保存"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("取消"))
        action_row.addWidget(self.buttons)
        outer.addLayout(action_row)
        self.reset_button.clicked.connect(self.restore_defaults)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        self._sync_controls()
        self._apply_preview()
        self.resize(540, 680)

    def _color(self, key: str) -> str:
        value = cast(str | None, getattr(self._preferences, key))
        return value or getattr(LIGHT.colors, key)

    def _choose_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self._color(key)), self, self.tr("选择颜色"))
        if color.isValid():
            value = color.name().upper()
            if key == "primary":
                self._preferences = replace(self._preferences, primary=value)
            elif key == "bg":
                self._preferences = replace(self._preferences, bg=value)
            elif key == "surface":
                self._preferences = replace(self._preferences, surface=value)
            else:
                self._preferences = replace(self._preferences, ink=value)
            self._apply_preview()

    def _system_font_changed(self, follows: bool) -> None:
        self.font_slider.setEnabled(not follows)
        self._preferences = replace(
            self._preferences,
            font_size=None if follows else self.font_slider.value(),
        )
        self._apply_preview()

    def _font_changed(self, value: int) -> None:
        if not self.system_font.isChecked():
            self._preferences = replace(self._preferences, font_size=value)
            self._apply_preview()

    def _sync_controls(self) -> None:
        for key, _ in _COLOR_FIELDS:
            self._labels[key].setText(self._color(key))
        follows = self._preferences.font_size is None
        self.system_font.blockSignals(True)
        self.system_font.setChecked(follows)
        self.system_font.blockSignals(False)
        self.font_slider.blockSignals(True)
        self.font_slider.setValue(
            system_body_size() if follows else int(self._preferences.font_size or 14)
        )
        self.font_slider.blockSignals(False)
        self.font_slider.setEnabled(not follows)
        self.font_value.setText(self.tr("系统") if follows else str(self._preferences.font_size))

    def _apply_preview(self) -> None:
        self._sync_controls()
        tokens = apply_appearance(self._app, self._preferences)
        self.preview.setStyleSheet(
            f"QFrame[role='card'] {{ background: {tokens.colors.surface}; }}"
        )
        self.preview_title.setStyleSheet(f"color: {tokens.colors.ink};")
        self.preview_text.setStyleSheet(f"color: {tokens.colors.ink};")
        self.preview_action.setStyleSheet(
            f"background: {tokens.colors.primary}; color: {tokens.colors.on_accent};"
        )
        low = (
            contrast_ratio(tokens.colors.ink, tokens.colors.bg) < 4.5
            or contrast_ratio(tokens.colors.ink, tokens.colors.surface) < 4.5
            or contrast_ratio(tokens.colors.on_accent, tokens.colors.primary) < 4.5
        )
        self.warning.setText(
            self.tr("部分文字与背景的对比度较低，请调整颜色以确保可读性。") if low else ""
        )

    def restore_defaults(self) -> None:
        """Preview the built-in light palette and system-following font size."""
        self._preferences = AppearancePreferences()
        self._apply_preview()

    def accept(self) -> None:
        """Save this account's preferences and close the dialog."""
        try:
            self._service.save(self._username, self._preferences)
        except (OSError, AppearanceSettingsError):
            _log.exception("APPEARANCE_SAVE_FAILED")
            self.warning.setText(self.tr("无法保存外观设置，请检查数据目录后重试。"))
            return
        super().accept()

    def reject(self) -> None:
        """Restore the saved appearance before closing without saving edits."""
        self._preferences = self._original
        apply_appearance(self._app, self._original)
        super().reject()
