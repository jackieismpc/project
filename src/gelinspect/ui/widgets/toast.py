"""Toast：非阻塞的短提示，浮在父窗口右上角，自动消失。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from shiboken6 import isValid

from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.util import repolish


class ToastKind(StrEnum):
    """Toast 类型。"""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


_ICONS = {
    ToastKind.INFO: ("info", "primary"),
    ToastKind.SUCCESS: ("circle-check", "success"),
    ToastKind.WARNING: ("triangle-alert", "warning"),
    ToastKind.DANGER: ("circle-x", "danger"),
}


class Toast(QFrame):
    """一条提示。使用 :meth:`show_message` 显示，同一父窗口同时只保留最新一条。"""

    def __init__(
        self,
        message: str,
        kind: ToastKind | str = ToastKind.INFO,
        parent: QWidget | None = None,
    ) -> None:
        """创建 Toast（尚未显示）。

        Args:
            message: 提示文字。
            kind: 类型。
            parent: 父窗口，Toast 相对它定位。
        """
        super().__init__(parent)
        kind = ToastKind(kind)
        self.setProperty("role", "toast")
        self.setProperty("tone", kind.value)
        self.setAccessibleName(message)
        self.setAccessibleDescription(kind.value)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(10)
        icon_name, color = _ICONS[kind]
        icon = Label()
        icon.setPixmap(IconLoader.pixmap(icon_name, 18, color))
        self.message_label = Label(message, "body")
        layout.addWidget(icon)
        layout.addWidget(self.message_label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        repolish(self)
        self.hide()

    def reposition(self) -> None:
        """贴到父窗口右上角（留 16px 边距）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(parent.width() - self.width() - 16, 16)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """父窗口改变大小时重新定位。

        Args:
            watched: 被监视对象。
            event: 事件。

        Returns:
            始终为 ``False``。
        """
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False

    def popup(self, timeout_ms: int = 3000) -> None:
        """显示并在超时后自动关闭。

        Args:
            timeout_ms: 显示时长（毫秒）；0 表示不自动关闭。
        """
        parent = self.parentWidget()
        if parent is not None:
            parent.installEventFilter(self)
        self.reposition()
        self.show()
        self.raise_()
        if timeout_ms > 0:
            self._timer.start(timeout_ms)

    def dismiss(self) -> None:
        """立即关闭并销毁。"""
        self._timer.stop()
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
            if getattr(parent, "_gel_toast", None) is self:
                parent._gel_toast = None  # type: ignore[attr-defined]
        self.hide()
        self.deleteLater()

    @staticmethod
    def show_message(
        parent: QWidget,
        message: str,
        kind: ToastKind | str = ToastKind.INFO,
        timeout_ms: int = 3000,
    ) -> Toast:
        """在父窗口上显示一条 Toast，替换尚未消失的旧提示。

        Args:
            parent: 父窗口。
            message: 提示文字。
            kind: 类型。
            timeout_ms: 显示时长（毫秒）。

        Returns:
            新建的 Toast。
        """
        # QObject 动态属性会留下已销毁 QObject 的指针；取属性时即可原生崩溃，
        # Python 的异常处理来不及兜底。使用 Python 引用并在访问 Qt 对象前检查有效性。
        previous = getattr(parent, "_gel_toast", None)
        if isinstance(previous, Toast) and isValid(previous):
            previous.dismiss()
        toast = Toast(message, kind, parent)
        parent._gel_toast = toast  # type: ignore[attr-defined]
        toast.popup(timeout_ms)
        return toast
