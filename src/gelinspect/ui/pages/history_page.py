"""历史记录页（最小入口）：列出已有的试验，双击打开回看。

完整的按时间、类型、批号、状态查询和导出是 P12 的工作；这里只保证已完成的试验可以随时
打开，回看结果、图像和录像（监控页只读模式）。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from gelinspect.interfaces.storage import ExperimentSummary
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.ui.labels import experiment_state_label, experiment_type_label
from gelinspect.ui.widgets.button import Button
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.label import Label

MAX_ROWS = 200
"""列表最多显示的试验数。"""


def _local(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


class HistoryPage(QWidget):
    """历史记录页。

    Signals:
        experimentRequested: 请求打开某个试验回看，参数为试验标识。
    """

    experimentRequested = Signal(str)  # noqa: N815

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._items: list[ExperimentSummary] = []
        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        col = QVBoxLayout(page)
        col.setContentsMargins(24, 20, 24, 28)
        col.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.hint = Label("", "meta")
        self.refresh_button = Button("")
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button = Button("")
        self.open_button.clicked.connect(self._open_selected)
        top.addWidget(self.title)
        top.addWidget(self.hint)
        top.addStretch(1)
        top.addWidget(self.refresh_button)
        top.addWidget(self.open_button)
        col.addLayout(top)
        self.card = Card()
        self.table = DataTable(self._columns(), fit_content=True)
        self.table.rowActivated.connect(self._on_activated)
        self.card.body.addWidget(self.table)
        col.addWidget(self.card)
        col.addStretch(1)
        self.retranslate()

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    def _columns(self) -> list[Column]:
        return [
            Column("id", self.tr("试验"), mono=True),
            Column("type", self.tr("类型")),
            Column("tubes", self.tr("试管"), mono=True, align_right=True),
            Column("state", self.tr("状态")),
            Column("operator", self.tr("操作人")),
            Column("created", self.tr("创建时间"), mono=True),
        ]

    def refresh(self) -> None:
        """重新读取试验列表。"""
        self._items = []
        if self._session is not None:
            try:
                self._items = self._services.queries.recent_experiments(self._session, MAX_ROWS)
            except AuthError:
                self._items = []
        self.table.set_rows(
            [
                {
                    "id": s.experiment_id,
                    "type": experiment_type_label(s.experiment_type),
                    "tubes": s.tube_count,
                    "state": experiment_state_label(s.state),
                    "operator": s.operator,
                    "created": _local(s.created_at),
                }
                for s in self._items
            ]
        )

    def _open_selected(self) -> None:
        index = self.table.view.currentIndex()
        if index.isValid():
            self._on_activated(index.row())

    def _on_activated(self, row: int) -> None:
        if 0 <= row < len(self._items):
            self.experimentRequested.emit(self._items[row].experiment_id)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("历史记录"))
        self.hint.setText(self.tr("双击一行打开回看；按条件查询和导出将在 P12 提供"))
        self.refresh_button.setText(self.tr("刷新"))
        self.open_button.setText(self.tr("打开所选试验"))
        self.table.set_columns(self._columns())
        self.table.set_empty_text(
            self.tr("还没有历史试验"), self.tr("完成第一次试验后，结果会显示在这里")
        )
        self.refresh()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
