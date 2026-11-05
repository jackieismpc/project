"""历史记录页：按试验类型、时间段、批号、状态和操作人筛选，分页，打开回看，导出 Excel。

查询走 ``QueryService``（服务层校验 ``experiment_view``），导出走 ``ExportService``
（``data_export``，实验员没有，按钮置灰）。导出放到后台线程，界面不冻结。
没有结果时显示空状态，并区分「还没有试验」和「没有符合条件的试验」。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QWidget

from gelinspect.domain.enums import ExperimentType
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ExperimentQuery, ExperimentSummary, PageRequest
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.export_service import ExportSummary, suggested_filename
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.export_labels import export_labels
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    experiment_state_label,
    experiment_type_label,
)
from gelinspect.ui.pages.export_password import PasswordProvider, ask_export_password
from gelinspect.ui.pages.page_frame import build_page_column, labeled
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.date_edit import OptionalDateEdit
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.pager import Pager
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)

PAGE_SIZE = 20
"""每页显示的试验数。"""


def _local(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


class HistoryPage(QWidget):
    """历史记录页。

    Signals:
        experimentRequested: 请求打开某个试验回看，参数为试验标识。
        exportFinished: 一次导出结束（成功或失败），参数为提示文字。
    """

    experimentRequested = Signal(str)  # noqa: N815
    exportFinished = Signal(str)  # noqa: N815

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self.export_password_provider: PasswordProvider = ask_export_password
        self._services = services
        self._session: Session | None = None
        self._items: list[ExperimentSummary] = []
        self._page = 0
        self._total = 0
        self._export_busy = False
        self._runner = BackgroundRunner(self)

        column, _scroll = build_page_column(self)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.hint = Label("", "meta")
        self.refresh_button = Button("")
        self.open_button = Button("")
        self.export_selected_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        self.export_list_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        top.addWidget(self.title)
        top.addWidget(self.hint)
        top.addStretch(1)
        for button in (
            self.refresh_button,
            self.open_button,
            self.export_selected_button,
            self.export_list_button,
        ):
            top.addWidget(button)
        column.addLayout(top)

        self.filter_card = Card()
        row = QHBoxLayout()
        row.setSpacing(12)
        self.type_label = Label("", "lab")
        self.type_box = QComboBox()
        self.type_box.setMinimumHeight(44)
        self.start_label = Label("", "lab")
        self.start_edit = OptionalDateEdit()
        self.end_label = Label("", "lab")
        self.end_edit = OptionalDateEdit()
        self.batch_label = Label("", "lab")
        self.batch_edit = QLineEdit()
        self.batch_edit.setMinimumHeight(44)
        self.state_label = Label("", "lab")
        self.state_box = QComboBox()
        self.state_box.setMinimumHeight(44)
        self.operator_label = Label("", "lab")
        self.operator_box = QComboBox()
        self.operator_box.setMinimumHeight(44)
        for label, field in (
            (self.type_label, self.type_box),
            (self.start_label, self.start_edit),
            (self.end_label, self.end_edit),
            (self.batch_label, self.batch_edit),
            (self.state_label, self.state_box),
            (self.operator_label, self.operator_box),
        ):
            row.addLayout(labeled(label, field), 1)
        self.search_button = Button("", ButtonVariant.PRIMARY, icon="search")
        self.reset_button = Button("")
        row.addWidget(self.search_button, 0)
        row.addWidget(self.reset_button, 0)
        row.setAlignment(self.search_button, Qt.AlignmentFlag.AlignBottom)
        row.setAlignment(self.reset_button, Qt.AlignmentFlag.AlignBottom)
        self.filter_card.body.addLayout(row)
        column.addWidget(self.filter_card)

        self.card = Card()
        self.table = DataTable(self._columns())
        self.table.set_multi_select(True)
        self.table.setMinimumHeight(330)
        self.pager = Pager()
        self.card.body.addWidget(self.table, 1)
        self.card.body.addWidget(self.pager)
        column.addWidget(self.card, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self._open_selected)
        self.search_button.clicked.connect(self.search)
        self.reset_button.clicked.connect(self.reset_filters)
        self.batch_edit.returnPressed.connect(self.search)
        self.export_selected_button.clicked.connect(self.request_export_selected)
        self.export_list_button.clicked.connect(self.request_export_list)
        self.table.rowActivated.connect(self._on_activated)
        self.table.selectionChanged.connect(self._update_buttons)
        self.pager.pageRequested.connect(self.load_page)
        self._fill_combos()
        self.retranslate()

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    @property
    def page_index(self) -> int:
        """当前页（从 0 起）。"""
        return self._page

    @property
    def total(self) -> int:
        """满足条件的总条数。"""
        return self._total

    @property
    def export_busy(self) -> bool:
        """是否有导出正在后台进行。"""
        return self._export_busy

    def items(self) -> list[ExperimentSummary]:
        """当前页的试验。

        Returns:
            试验摘要列表。
        """
        return list(self._items)

    def current_query(self) -> ExperimentQuery:
        """按筛选控件生成查询条件。

        Returns:
            查询条件；日期按本地时区，结束日期含当天。
        """
        start_day = self.start_edit.value()
        end_day = self.end_edit.value()
        type_value = self.type_box.currentData()
        state_value = self.state_box.currentData()
        return ExperimentQuery(
            experiment_type=None if type_value is None else ExperimentType(type_value),
            start=None if start_day is None else datetime.combine(start_day, time.min).astimezone(),
            end=(
                None
                if end_day is None
                else datetime.combine(end_day + timedelta(days=1), time.min).astimezone()
            ),
            batch_no=self.batch_edit.text().strip() or None,
            state=None if state_value is None else ExperimentState(state_value),
            operator=self.operator_box.currentData(),
        )

    def empty_title(self) -> str:
        """空状态的标题文字（测试用）。

        Returns:
            当前空状态标题。
        """
        return self.table.empty_title_text()

    def filters_active(self) -> bool:
        """是否设置了任何筛选条件。"""
        return self.current_query() != ExperimentQuery()

    def refresh(self) -> None:
        """重新读取操作人列表和当前页。"""
        self._load_operators()
        self.load_page(self._page)

    def search(self) -> None:
        """按当前筛选条件从第一页开始查询。"""
        self.load_page(0)

    def reset_filters(self) -> None:
        """清空全部筛选条件并重新查询。"""
        self.type_box.setCurrentIndex(0)
        self.state_box.setCurrentIndex(0)
        self.operator_box.setCurrentIndex(0)
        self.start_edit.set_value(None)
        self.end_edit.set_value(None)
        self.batch_edit.clear()
        self.load_page(0)

    def load_page(self, page: int) -> None:
        """加载某一页；超出末页时落到末页。

        Args:
            page: 页号（从 0 起）。
        """
        self._items, self._total = [], 0
        if self._session is not None:
            try:
                query = self.current_query()
                result = self._services.queries.search_experiments(
                    self._session,
                    query,
                    PageRequest(offset=max(page, 0) * PAGE_SIZE, limit=PAGE_SIZE),
                )
                if not result.items and result.total > 0 and page > 0:
                    last = (result.total - 1) // PAGE_SIZE
                    result = self._services.queries.search_experiments(
                        self._session, query, PageRequest(offset=last * PAGE_SIZE, limit=PAGE_SIZE)
                    )
                    page = last
                self._items, self._total = list(result.items), result.total
            except AuthError as error:
                _log.info("历史记录查询被拒绝：%s", error)
        self._page = max(page, 0)
        self._render()

    def _load_operators(self) -> None:
        names: list[str] = []
        if self._session is not None:
            try:
                names = self._services.queries.operators(self._session)
            except AuthError:
                names = []
        keep = self.operator_box.currentData()
        self.operator_box.blockSignals(True)
        self.operator_box.clear()
        self.operator_box.addItem(self.tr("全部操作人"), None)
        for name in names:
            self.operator_box.addItem(name, name)
        index = self.operator_box.findData(keep)
        self.operator_box.setCurrentIndex(max(index, 0))
        self.operator_box.blockSignals(False)

    def _render(self) -> None:
        self.table.set_rows(
            [
                {
                    "id": s.experiment_id,
                    "type": experiment_type_label(s.experiment_type),
                    "tubes": s.tube_count,
                    "state": experiment_state_label(s.state),
                    "operator": s.operator,
                    "created": _local(s.created_at),
                    "completed": "" if s.completed_at is None else _local(s.completed_at),
                    "signed": "" if s.signed_at is None else _local(s.signed_at),
                }
                for s in self._items
            ]
        )
        if self.filters_active():
            self.table.set_empty_text(
                self.tr("没有符合条件的试验"), self.tr("调整筛选条件，或点「重置」清除条件后再查")
            )
        else:
            self.table.set_empty_text(
                self.tr("还没有历史试验"), self.tr("完成第一次试验后，结果会显示在这里")
            )
        self.pager.set_state(page=self._page, total=self._total, page_size=PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self) -> None:
        session = self._session
        can_export = session is not None and session.can(Permission.DATA_EXPORT)
        selected = self.table.selected_rows()
        busy = self._export_busy
        self.open_button.setEnabled(bool(self._items))
        self.export_selected_button.setEnabled(can_export and bool(selected) and not busy)
        self.export_list_button.setEnabled(can_export and self._total > 0 and not busy)
        tip = "" if can_export else self.tr("当前角色无此权限")
        self.export_selected_button.setToolTip(
            tip or ("" if selected else self.tr("先在列表里点选要导出的试验"))
        )
        self.export_list_button.setToolTip(tip)

    # ------------------------------------------------------------------ 打开

    def _open_selected(self) -> None:
        rows = self.table.selected_rows()
        index = self.table.view.currentIndex()
        if rows:
            self._on_activated(rows[0])
        elif index.isValid():
            self._on_activated(index.row())

    def _on_activated(self, row: int) -> None:
        if 0 <= row < len(self._items):
            self.experimentRequested.emit(self._items[row].experiment_id)

    # ------------------------------------------------------------------ 导出

    def request_export_selected(self) -> None:
        """选择文件并导出选中试验的结果（含结论、λc、单管特征和复核）。"""
        if not self.table.selected_rows():
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出所选试验结果"),
            suggested_filename("results"),
            self.tr("Excel 文件 (*.xlsx)"),
        )
        if target:
            self.export_selected_to(Path(target))

    def export_selected_to(self, destination: Path) -> None:
        """把选中试验的结果导出到文件（后台线程）。

        Args:
            destination: 目标文件。
        """
        session = self._session
        ids = [self._items[row].experiment_id for row in self.table.selected_rows()]
        if session is None or not ids:
            return
        labels = export_labels()
        password, ok = self._export_password()
        if not ok:
            return
        self._start_export(
            lambda: self._services.exports.export_results(
                session, ids, destination, labels, password=password
            )
        )

    def request_export_list(self) -> None:
        """选择文件并导出符合当前筛选条件的全部试验（不只当前页）。"""
        target, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出历史列表"),
            suggested_filename("history"),
            self.tr("Excel 文件 (*.xlsx)"),
        )
        if target:
            self.export_list_to(Path(target))

    def export_list_to(self, destination: Path) -> None:
        """把符合当前筛选条件的历史列表导出到文件（后台线程）。

        Args:
            destination: 目标文件。
        """
        session = self._session
        if session is None:
            return
        query = self.current_query()
        labels = export_labels()
        password, ok = self._export_password()
        if not ok:
            return
        self._start_export(
            lambda: self._services.exports.export_history(
                session, query, destination, labels, password=password
            )
        )

    def _export_password(self) -> tuple[str | None, bool]:
        """开启了导出加密时先要密码。返回（密码，是否继续）。"""
        if not self._services.exports.encryption_required:
            return None, True
        password = self.export_password_provider(self)
        return password, password is not None

    def _start_export(self, job: Callable[[], ExportSummary]) -> None:
        self._export_busy = True
        self._update_buttons()
        self._runner.submit(job, self._export_done, self._export_failed)

    def _export_done(self, summary: ExportSummary) -> None:
        self._export_busy = False
        text = self.tr("已导出 {rows} 行：{name}").format(rows=summary.rows, name=summary.path.name)
        Toast.show_message(self.window(), text, ToastKind.SUCCESS)
        self._update_buttons()
        self.exportFinished.emit(text)

    def _export_failed(self, error: Exception) -> None:
        self._export_busy = False
        if isinstance(error, ServiceError):
            text = describe_service_error(error)
        elif isinstance(error, AuthError):
            text = describe_auth_error(error)
        else:
            text = self.tr("导出失败：{reason}").format(reason=str(error))
        Toast.show_message(self.window(), text, ToastKind.WARNING)
        self._update_buttons()
        self.exportFinished.emit(text)

    # ------------------------------------------------------------------ 文字

    def _columns(self) -> list[Column]:
        return [
            Column("id", self.tr("试验"), mono=True),
            Column("type", self.tr("类型")),
            Column("tubes", self.tr("试管"), mono=True, align_right=True),
            Column("state", self.tr("状态")),
            Column("operator", self.tr("操作人")),
            Column("created", self.tr("创建时间"), mono=True),
            Column("completed", self.tr("完成时间"), mono=True),
            Column("signed", self.tr("签署时间"), mono=True),
        ]

    def _fill_combos(self) -> None:
        for box, first, entries in (
            (
                self.type_box,
                self.tr("全部类型"),
                [(experiment_type_label(t), t.value) for t in ExperimentType],
            ),
            (
                self.state_box,
                self.tr("全部状态"),
                [(experiment_state_label(s), s.value) for s in ExperimentState],
            ),
        ):
            keep = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem(first, None)
            for text, value in entries:
                box.addItem(text, value)
            box.setCurrentIndex(max(box.findData(keep), 0))
            box.blockSignals(False)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("历史记录"))
        self.hint.setText(self.tr("双击一行打开回看，点选多行可以导出"))
        self.refresh_button.setText(self.tr("刷新"))
        self.open_button.setText(self.tr("打开所选试验"))
        self.export_selected_button.setText(self.tr("导出所选结果…"))
        self.export_list_button.setText(self.tr("导出当前列表…"))
        self.type_label.setText(self.tr("试验类型"))
        self.start_label.setText(self.tr("开始日期"))
        self.end_label.setText(self.tr("结束日期"))
        self.batch_label.setText(self.tr("批号"))
        self.batch_edit.setPlaceholderText(self.tr("鲎试剂或样品"))
        self.state_label.setText(self.tr("状态"))
        self.operator_label.setText(self.tr("操作人"))
        self.search_button.setText(self.tr("查询"))
        self.reset_button.setText(self.tr("重置"))
        self.table.set_columns(self._columns())
        self._fill_combos()
        self._load_operators()
        self._render()
        self.pager.retranslate()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
