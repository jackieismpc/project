"""统一的「建设中」占位页：说明页面将包含什么。"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.ui.routes import Route, route_title
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label


class PlaceholderPage(QWidget):
    """尚未实现的页面。"""

    def __init__(self, route: Route, parent: QWidget | None = None) -> None:
        """创建占位页。

        Args:
            route: 页面路由，提供标题和说明。
            parent: 父控件。
        """
        super().__init__(parent)
        self.route = route
        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 28)
        outer.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.badge = StatusBadge("", BadgeTone.WARNING)
        top.addWidget(self.title)
        top.addWidget(self.badge)
        top.addStretch(1)
        outer.addLayout(top)
        card = Card()
        card.body.setSpacing(8)
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("construction", 36, "ink_muted"))
        icon.setAlignment(Qt.AlignmentFlag.AlignLeft)
        card.body.addWidget(icon)
        self.headline = Label("", "val")
        self.summary = Label("", "body")
        self.summary.setWordWrap(True)
        card.body.addWidget(self.headline)
        card.body.addWidget(self.summary)
        outer.addWidget(card)
        outer.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(route_title(self.route))
        self.badge.set_text(self.tr("建设中"))
        self.headline.setText(self.tr("这个页面还在建设中"))
        self.summary.setText(QCoreApplication.translate("Routes", self.route.summary))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
