"""审计追踪页：按时间段、操作者、关键字查询，校验哈希链，导出 Excel。

字段与参考软件一致：序号、操作者、时间、权限（操作时的角色）、操作记录。需要 ``audit_view``；
查询、校验和导出都在后台线程执行（10 万条的校验约 0.4 秒，导出更久），界面不冻结，
校验显示进度。**查看审计本身也写审计**（服务层负责）：新开一次查询写一条，往后翻页不重复写。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QWidget

from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import AuditRecord, ChainFailure, ChainVerification, PageRequest
from gelinspect.services.app_services import AppServices
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.export_service import ExportSummary, suggested_filename
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.export_labels import export_labels
from gelinspect.ui.labels import describe_auth_error, describe_service_error, role_label
from gelinspect.ui.pages.page_frame import build_page_column, labeled
from gelinspect.ui.pages.wizard_widgets import Banner
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.date_edit import OptionalDateEdit
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.pager import Pager
from gelinspect.ui.widgets.progress_bar import ProgressBar
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)

PAGE_SIZE = 50
"""每页显示的审计记录数。"""


def _stamp(record: AuditRecord) -> str:
    return record.event.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")


class AuditPage(QWidget):
    """审计追踪页。

    Signals:
        loaded: 一次查询的结果已显示。
        verified: 一次校验已结束并显示了结果。
        exportFinished: 一次导出结束（成功或失败），参数为提示文字。
    """

    loaded = Signal()
    verified = Signal()
    exportFinished = Signal(str)  # noqa: N815
    _progressed = Signal(int, int)

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._records: list[AuditRecord] = []
        self._page = 0
        self._total = 0
        self._loading = False
        self._verifying = False
        self._exporting = False
        self._query_token: int | None = None
        self._snapshot_seq: int | None = None
        self._bad_seq: int | None = None
        self._error_text = ""
        self._last_verification: ChainVerification | None = None
        self._banner_content: Callable[[], tuple[str, str, str, str]] | None = None
        self._runner = BackgroundRunner(self)

        column, _scroll = build_page_column(self)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.status = Label("", "meta")
        self.verify_button = Button("", ButtonVariant.PRIMARY, icon="shield-check")
        self.export_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        top.addWidget(self.title)
        top.addWidget(self.status)
        top.addStretch(1)
        top.addWidget(self.verify_button)
        top.addWidget(self.export_button)
        column.addLayout(top)

        self.filter_card = Card()
        row = QHBoxLayout()
        row.setSpacing(12)
        self.start_label = Label("", "lab")
        self.start_edit = OptionalDateEdit()
        self.end_label = Label("", "lab")
        self.end_edit = OptionalDateEdit()
        self.actor_label = Label("", "lab")
        self.actor_box = QComboBox()
        self.actor_box.setEditable(True)
        self.actor_box.setMinimumHeight(44)
        self.keyword_label = Label("", "lab")
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setMinimumHeight(44)
        for label, field, weight in (
            (self.start_label, self.start_edit, 1),
            (self.end_label, self.end_edit, 1),
            (self.actor_label, self.actor_box, 1),
            (self.keyword_label, self.keyword_edit, 2),
        ):
            row.addLayout(labeled(label, field), weight)
        self.search_button = Button("", ButtonVariant.PRIMARY, icon="search")
        self.reset_button = Button("")
        row.addWidget(self.search_button)
        row.addWidget(self.reset_button)
        row.setAlignment(self.search_button, Qt.AlignmentFlag.AlignBottom)
        row.setAlignment(self.reset_button, Qt.AlignmentFlag.AlignBottom)
        self.filter_card.body.addLayout(row)
        column.addWidget(self.filter_card)

        self.banner = Banner()
        column.addWidget(self.banner)
        self.progress_card = Card()
        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.progress_label = Label("", "body")
        self.progress_bar = ProgressBar()
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress_bar, 1)
        self.progress_card.body.addLayout(progress_row)
        self.progress_card.setVisible(False)
        column.addWidget(self.progress_card)

        self.card = Card()
        self.table = DataTable(self._columns())
        self.table.setMinimumHeight(360)
        self.pager = Pager()
        self.card.body.addWidget(self.table, 1)
        self.card.body.addWidget(self.pager)
        column.addWidget(self.card, 1)

        self.search_button.clicked.connect(self.search)
        self.reset_button.clicked.connect(self.reset_filters)
        self.keyword_edit.returnPressed.connect(self.search)
        self.verify_button.clicked.connect(self.verify_integrity)
        self.export_button.clicked.connect(self.request_export)
        self.pager.pageRequested.connect(self.load_page)
        self._progressed.connect(self._on_progress)
        self.retranslate()

    # ------------------------------------------------------------------ 属性

    @property
    def loading(self) -> bool:
        """是否有查询在后台进行。"""
        return self._loading

    @property
    def verifying(self) -> bool:
        """是否有校验在后台进行。"""
        return self._verifying

    @property
    def exporting(self) -> bool:
        """是否有导出在后台进行。"""
        return self._exporting

    @property
    def page_index(self) -> int:
        """当前页（从 0 起）。"""
        return self._page

    @property
    def total(self) -> int:
        """满足条件的总条数。"""
        return self._total

    @property
    def records(self) -> list[AuditRecord]:
        """当前页的记录（最新在前）。"""
        return list(self._records)

    @property
    def last_verification(self) -> ChainVerification | None:
        """最近一次校验的结果。"""
        return self._last_verification

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """等后台任务跑完（不处理回调），关闭窗口和测试清理用。

        Args:
            timeout_ms: 时限。

        Returns:
            是否已全部结束。
        """
        return self._runner.wait(timeout_ms)

    # ------------------------------------------------------------------ 查询

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    def current_filter(self) -> AuditFilter:
        """按筛选控件生成查询条件。

        Returns:
            查询条件；日期按本地时区，结束日期含当天。
        """
        start_day = self.start_edit.value()
        end_day = self.end_edit.value()
        return AuditFilter(
            start=None if start_day is None else datetime.combine(start_day, time.min).astimezone(),
            end=(
                None
                if end_day is None
                else datetime.combine(end_day + timedelta(days=1), time.min).astimezone()
            ),
            actor=self.actor_box.currentText().strip() or None,
            keyword=self.keyword_edit.text().strip() or None,
        )

    def refresh(self) -> None:
        """进入页面时：刷新操作者列表，从第一页重新查询。"""
        self._load_actors()
        self.load_page(0)

    def search(self) -> None:
        """按当前条件从第一页开始查询。"""
        self.load_page(0)

    def reset_filters(self) -> None:
        """清空全部条件并重新查询。"""
        self.start_edit.set_value(None)
        self.end_edit.set_value(None)
        self.actor_box.setCurrentIndex(0)
        self.actor_box.setEditText("")
        self.keyword_edit.clear()
        self.load_page(0)

    def load_page(self, page: int) -> None:
        """在后台加载某一页；新的请求让还没返回的旧请求作废。

        Args:
            page: 页号（从 0 起）。
        """
        session = self._session
        if session is None:
            return
        audit_filter = self.current_filter()
        if page > 0:  # 翻页：沿用第一页看到的范围，期间新写入的记录不把后面的页顶歪
            audit_filter = replace(audit_filter, max_seq=self._snapshot_seq)
        request = PageRequest(offset=max(page, 0) * PAGE_SIZE, limit=PAGE_SIZE)
        if self._query_token is not None:
            self._runner.cancel(self._query_token)
        self._loading = True
        self._error_text = ""
        self._sync_state()
        self._query_token = self._runner.submit(
            lambda: self._services.audit.query(session, audit_filter, request),
            lambda result: self._on_loaded(max(page, 0), result.items, result.total),
            self._on_query_failed,
        )

    def _on_loaded(self, page: int, items: tuple[AuditRecord, ...], total: int) -> None:
        self._loading = False
        self._query_token = None
        if not items and total > 0 and page > 0:
            self.load_page((total - 1) // PAGE_SIZE)  # 记录变少后落到末页
            return
        self._records, self._total, self._page = list(items), total, page
        if page == 0:
            self._snapshot_seq = items[0].seq if items else None
        self._render()
        self.loaded.emit()

    def _on_query_failed(self, error: Exception) -> None:
        self._loading = False
        self._query_token = None
        self._records, self._total = [], 0
        self._error_text = self._describe(error)
        self._render()
        self.loaded.emit()

    def _load_actors(self) -> None:
        names: list[str] = []
        if self._session is not None:
            try:
                names = self._services.audit.actors(self._session)
            except AuthError:
                names = []
        keep = self.actor_box.currentText()
        self.actor_box.blockSignals(True)
        self.actor_box.clear()
        self.actor_box.addItem("")
        self.actor_box.addItems(names)
        self.actor_box.setEditText(keep)
        self.actor_box.blockSignals(False)

    def _render(self) -> None:
        bad = self._bad_seq
        self.table.set_rows(
            [
                {
                    "seq": r.seq,
                    "actor": r.event.actor,
                    "time": _stamp(r),
                    "role": "" if r.event.actor_role is None else role_label(r.event.actor_role),
                    "detail": r.event.detail,
                    **({"_tone": "danger"} if bad is not None and r.seq == bad else {}),
                }
                for r in self._records
            ]
        )
        active = self.current_filter() != AuditFilter()
        if self._error_text:
            self.table.set_empty_text(self._error_text, "")
        elif active:
            self.table.set_empty_text(
                self.tr("没有符合条件的审计记录"),
                self.tr("调整查询条件，或点「重置」清除条件后再查"),
            )
        else:
            self.table.set_empty_text(self.tr("还没有审计记录"), "")
        self.pager.set_state(page=self._page, total=self._total, page_size=PAGE_SIZE)
        self._sync_state()

    def _sync_state(self) -> None:
        self.status.setText(self.tr("正在查询……") if self._loading else "")
        self.verify_button.setEnabled(not self._verifying)
        self.search_button.setEnabled(not self._exporting)
        session = self._session
        can_export = session is not None and session.can(Permission.DATA_EXPORT)
        self.export_button.setEnabled(can_export and not self._exporting and self._total > 0)
        self.export_button.setToolTip("" if can_export else self.tr("当前角色无此权限"))

    # ------------------------------------------------------------------ 校验

    def verify_integrity(self) -> None:
        """在后台校验整条哈希链，显示进度，结束后用横幅给出结果。"""
        session = self._session
        if session is None or self._verifying:
            return
        self._verifying = True
        self._banner_content = None
        self.banner.clear()
        self.progress_bar.set_value(0.0)
        self.progress_label.setText(self.tr("正在校验完整性……"))
        self.progress_card.setVisible(True)
        self._sync_state()

        def work() -> ChainVerification:
            return self._services.audit.verify_chain(session, self._progressed.emit)

        self._runner.submit(work, self._on_verified, self._on_verify_failed)

    def _on_progress(self, done: int, total: int) -> None:
        if not self._verifying:
            return
        self.progress_bar.set_value(done / total if total else 1.0)
        self.progress_label.setText(
            self.tr("正在校验完整性：{done} / {total}").format(done=done, total=total)
        )

    def _on_verified(self, result: ChainVerification) -> None:
        self._verifying = False
        self._last_verification = result
        self.progress_card.setVisible(False)
        self._bad_seq = None if result.ok else result.first_bad_seq
        self._banner_content = lambda: self._verification_banner(result)
        self._show_banner()
        self._render()
        self.verified.emit()

    def _verification_banner(self, result: ChainVerification) -> tuple[str, str, str, str]:
        """校验结果横幅的（色调、图标、标题、说明），按当前语言生成。"""
        if result.ok:
            return (
                "success",
                "shield-check",
                self.tr("校验通过：哈希链完好"),
                self.tr("共校验 {count} 条记录，没有发现被改动或删除的记录。").format(
                    count=result.checked
                ),
            )
        where = (
            self.tr("链头记录缺失")
            if result.first_bad_seq is None
            else self.tr("第一处异常在序号 {seq}").format(seq=result.first_bad_seq)
        )
        return (
            "danger",
            "triangle-alert",
            self.tr("校验未通过：审计记录可能被改动或删除"),
            f"{where}。{self._failure_text(result.failure)}",
        )

    def _show_banner(self) -> None:
        if self._banner_content is None:
            self.banner.clear()
            return
        tone, icon, title, detail = self._banner_content()
        self.banner.show_message(tone, icon, title, detail)

    def _on_verify_failed(self, error: Exception) -> None:
        self._verifying = False
        self.progress_card.setVisible(False)
        reason = self._describe(error)
        self._banner_content = lambda: (
            "warning",
            "triangle-alert",
            self.tr("校验没有完成"),
            reason,
        )
        self._show_banner()
        self._sync_state()
        self.verified.emit()

    def _failure_text(self, failure: ChainFailure | None) -> str:
        return {
            ChainFailure.HASH_MISMATCH: self.tr("这条记录的内容与它的哈希不符，内容可能被改动。"),
            ChainFailure.LINK_BROKEN: self.tr("这条记录与上一条的哈希衔接不上，前一条可能被改动。"),
            ChainFailure.MISSING: self.tr("序号不连续，这个序号的记录可能被删除。"),
            ChainFailure.HEAD_MISMATCH: self.tr("链头与最后一条记录不符，末尾的记录可能被删除。"),
            None: "",
        }[failure]

    # ------------------------------------------------------------------ 导出

    def request_export(self) -> None:
        """选择文件并导出符合当前条件的全部审计记录。"""
        target, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出审计追踪"),
            suggested_filename("audit"),
            self.tr("Excel 文件 (*.xlsx)"),
        )
        if target:
            self.export_to(Path(target))

    def export_to(self, destination: Path) -> None:
        """把符合当前条件的全部审计记录导出到文件（后台线程）。

        Args:
            destination: 目标文件。
        """
        session = self._session
        if session is None or self._exporting:
            return
        audit_filter = self.current_filter()
        labels = export_labels()
        self._exporting = True
        self._sync_state()

        def job() -> ExportSummary:
            return self._services.exports.export_audit(session, audit_filter, destination, labels)

        self._runner.submit(job, self._export_done, self._export_failed)

    def _export_done(self, summary: ExportSummary) -> None:
        self._exporting = False
        text = self.tr("已导出 {rows} 条：{name}").format(rows=summary.rows, name=summary.path.name)
        Toast.show_message(self.window(), text, ToastKind.SUCCESS)
        self._sync_state()
        self.exportFinished.emit(text)

    def _export_failed(self, error: Exception) -> None:
        self._exporting = False
        text = self._describe(error)
        Toast.show_message(self.window(), text, ToastKind.WARNING)
        self._sync_state()
        self.exportFinished.emit(text)

    def _describe(self, error: Exception) -> str:
        if isinstance(error, ServiceError):
            return describe_service_error(error)
        if isinstance(error, AuthError):
            return describe_auth_error(error)
        return self.tr("操作失败：{reason}").format(reason=str(error))

    # ------------------------------------------------------------------ 文字

    def _columns(self) -> list[Column]:
        return [
            Column("seq", self.tr("序号"), mono=True, align_right=True),
            Column("actor", self.tr("操作者")),
            Column("time", self.tr("时间"), mono=True),
            Column("role", self.tr("权限")),
            Column("detail", self.tr("操作记录")),
        ]

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("审计追踪"))
        self.verify_button.setText(self.tr("校验完整性"))
        self.export_button.setText(self.tr("导出 Excel…"))
        self.start_label.setText(self.tr("开始日期"))
        self.end_label.setText(self.tr("结束日期"))
        self.actor_label.setText(self.tr("操作者"))
        line_edit = self.actor_box.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(self.tr("全部操作者"))
        self.keyword_label.setText(self.tr("关键字"))
        self.keyword_edit.setPlaceholderText(self.tr("操作记录或试验编号"))
        self.search_button.setText(self.tr("查询"))
        self.reset_button.setText(self.tr("重置"))
        self.table.set_columns(self._columns())
        self._render()
        self._show_banner()
        self.pager.retranslate()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
