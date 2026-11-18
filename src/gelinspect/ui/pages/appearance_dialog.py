"""个人外观偏好编辑窗口，提供即时预览和取消回滚。"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import cast

from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLayout,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gelinspect.services.appearance_service import AppearancePreferences, AppearanceService
from gelinspect.ui.theme.appearance import apply_appearance
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.label import Label

_log = logging.getLogger(__name__)


class AppearanceDialog(QDialog):
    """为单个账号选择固定配色和字号档位。"""

    def __init__(
        self,
        username: str,
        service: AppearanceService,
        parent: QWidget | None = None,
        *,
        app: QApplication | None = None,
    ) -> None:
        """创建即时预览窗口。

        Args:
            username: 保存偏好的账号名。
            service: 外观偏好服务。
            parent: 可选父窗口。
            app: 应用对象，默认使用当前 QApplication。
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("外观设置"))
        self.setMinimumWidth(440)
        self._username = username
        app_instance = app or cast(QApplication | None, QApplication.instance())
        if app_instance is None:
            raise RuntimeError("AppearanceDialog requires QApplication")
        self._app = app_instance
        self._service = service
        self._original = self._service.get(username)
        self._preferences = self._original
        self.palette_buttons: dict[str, QRadioButton] = {}
        self.font_buttons: dict[str, QRadioButton] = {}

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
            self.tr("按账号保存外观。选择固定配色和字号档位，更改立即预览；取消可恢复原设置。"),
            "meta",
        )
        description.setWordWrap(True)
        root.addWidget(description)

        self.palette_group = QButtonGroup(self)
        palette_box = QVBoxLayout()
        palette_box.addWidget(Label(self.tr("配色"), "lab"))
        for key, text in (("teal", self.tr("石青")), ("blue", self.tr("沉静蓝"))):
            button = QRadioButton(text)
            self.palette_group.addButton(button)
            palette_box.addWidget(button)
            self.palette_buttons[key] = button
            button.toggled.connect(
                lambda checked, palette=key: self._palette_changed(palette, checked)
            )
        root.addLayout(palette_box)

        self.font_group = QButtonGroup(self)
        font_box = QVBoxLayout()
        font_box.addWidget(Label(self.tr("字号"), "lab"))
        for key, text in (
            ("system", self.tr("跟随系统")),
            ("small", self.tr("较小")),
            ("large", self.tr("较大")),
        ):
            button = QRadioButton(text)
            self.font_group.addButton(button)
            font_box.addWidget(button)
            self.font_buttons[key] = button
            button.toggled.connect(
                lambda checked, scale=key: self._font_scale_changed(scale, checked)
            )
        root.addLayout(font_box)

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
        self.resize(540, 520)

    def _palette_changed(self, palette: str, checked: bool) -> None:
        if checked:
            self._preferences = replace(self._preferences, palette=palette)
            self._apply_preview()

    def _font_scale_changed(self, scale: str, checked: bool) -> None:
        if checked:
            self._preferences = replace(self._preferences, font_scale=scale)
            self._apply_preview()

    def _sync_controls(self) -> None:
        for key, button in self.palette_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == self._preferences.palette)
            button.blockSignals(False)
        for key, button in self.font_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == self._preferences.font_scale)
            button.blockSignals(False)

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

    def restore_defaults(self) -> None:
        """即时预览默认石青配色和系统字号。"""
        self._preferences = AppearancePreferences()
        self._apply_preview()

    def accept(self) -> None:
        """保存当前账号偏好并关闭窗口。"""
        try:
            self._service.save(self._username, self._preferences)
        except (OSError, ValueError):
            _log.exception("APPEARANCE_SAVE_FAILED")
            self.warning.setText(self.tr("无法保存外观设置，请检查数据目录后重试。"))
            return
        super().accept()

    def reject(self) -> None:
        """取消更改并恢复打开窗口前的主题。"""
        self._preferences = self._original
        apply_appearance(self._app, self._original)
        super().reject()
