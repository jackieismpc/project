"""报告中心：左边纸面预览，右边签名状态和操作（对照 ``docs/design/Report.dc.html``）。

页面不碰数据库和文件：预览、签署、验签、导出都走 ``ReportService``，权限由服务层再校验一遍。
预览用 ``QPrintPreviewWidget``，画的是和 PDF 同一套排版（``ReportService.paint``）。

* 单个试验的报告：选试验、选语言（已签署的固定为签署时的语言）、签署（再次输入密码）、
  重新验签、打印、导出已签名 PDF。
* 单批次报告：选批号种类和批号，汇总该批号下已完成的试验；批次里的试验全部签署后才能签署。
* 签名用本机自签证书：能防篡改，没有第三方 CA 的法律效力，界面常驻说明。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewWidget
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import BatchKind, ReportScope
from gelinspect.domain.permissions import Permission
from gelinspect.domain.signing import SigningBlocker
from gelinspect.interfaces.storage import ExperimentSummary, ReportRecord, StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.reporting.renderer import configure_printer
from gelinspect.services.reporting.report_service import (
    ExportResult,
    ReportPreview,
    SignedReport,
)
from gelinspect.services.reporting.texts import ReportLanguage, report_language_of
from gelinspect.services.reporting.verify import SignatureVerification
from gelinspect.ui.labels import (
    conclusion_verdict_label,
    describe_service_error,
    describe_storage_busy,
    experiment_state_label,
    experiment_type_label,
)
from gelinspect.ui.pages.report_dialogs import BatchExportDialog, SignDialog
from gelinspect.ui.report_labels import (
    describe_signing_blocks,
    describe_verification,
    self_signed_notice,
)
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)

MAX_EXPERIMENTS = 200
"""下拉框里最多列出的试验数。"""
PANEL_WIDTH = 330
"""右侧操作栏宽度。"""
_LANGUAGE_NAMES = {ReportLanguage.ZH_CN: "简体中文", ReportLanguage.EN: "English"}
"""报告语言自己的名字，不翻译。"""


def _stamp(value: datetime | None) -> str:
    return "–" if value is None else value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _short(digest: str | None) -> str:
    return "–" if not digest else f"{digest[:8]}…{digest[-6:]}"


class ReportsPage(QWidget):
    """报告中心页。

    Signals:
        signedExperiment: 签署成功，参数为试验标识（监控页、工作台据此刷新）。
        navigationRequested: 请求跳转到某页面，参数为路由标识。
    """

    signedExperiment = Signal(str)  # noqa: N815
    navigationRequested = Signal(str)  # noqa: N815

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._experiments: list[ExperimentSummary] = []
        self._preview: ReportPreview | None = None
        self._batch: tuple[BatchKind, str] | None = None
        self._ui_language = ReportLanguage.ZH_CN
        self._language_chosen = False
        self._record: ReportRecord | None = None
        self._verification: tuple[SignatureVerification, str, bool] | None = None
        self._busy = False
        self._loading = False
        self.setProperty("role", "root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        # 滚动条常驻：预览按宽度缩放、文字按宽度换行，滚动条时有时无会让布局来回震荡。
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        self._column = QVBoxLayout(page)
        self._column.setContentsMargins(24, 20, 24, 28)
        self._column.setSpacing(14)

        self._build_header()
        self._build_empty()
        self._build_content()
        self._column.addStretch(1)
        self.retranslate()
        self._show_empty(True)

    # ------------------------------------------------------------------ 搭建

    def _build_header(self) -> None:
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.meta = Label("", "meta", mono=True)
        self.experiment_label = Label("", "lab")
        self.experiment_box = QComboBox()
        self.experiment_box.setMinimumWidth(300)
        self.experiment_box.setMaximumWidth(460)
        self.refresh_button = Button("", ButtonVariant.SECONDARY, icon="rotate-ccw")
        top.addWidget(self.title)
        top.addWidget(self.meta)
        top.addStretch(1)
        top.addWidget(self.experiment_label)
        top.addWidget(self.experiment_box)
        top.addWidget(self.refresh_button)
        self.header = QWidget()
        self.header.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        self._column.addWidget(self.header)
        self.experiment_box.currentIndexChanged.connect(self._on_experiment_changed)
        self.refresh_button.clicked.connect(self.refresh)

    def _build_empty(self) -> None:
        self.empty_card = Card()
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("file-text", 36, "ink_muted"))
        self.empty_title = Label("", "val")
        self.empty_hint = Label("", "body")
        self.empty_hint.setWordWrap(True)
        self.empty_button = Button("", ButtonVariant.PRIMARY)
        self.empty_button.clicked.connect(lambda: self.navigationRequested.emit("history"))
        for widget in (icon, self.empty_title, self.empty_hint):
            self.empty_card.body.addWidget(widget)
        self.empty_card.body.addWidget(self.empty_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._column.addWidget(self.empty_card)

    def _build_content(self) -> None:
        self.content = QWidget()
        row = QHBoxLayout(self.content)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        row.addWidget(self._build_desk(), 1)
        row.addWidget(self._build_panel(), 0, Qt.AlignmentFlag.AlignTop)
        self._column.addWidget(self.content)

    def _build_desk(self) -> QWidget:
        self.desk = QFrame()
        self.desk.setProperty("role", "desk")
        layout = QVBoxLayout(self.desk)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(8)
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_printer(self._printer)
        self.preview_widget = QPrintPreviewWidget(self._printer)
        self.preview_widget.setMinimumHeight(640)
        self.preview_widget.setMinimumWidth(420)
        self.preview_widget.setZoomMode(QPrintPreviewWidget.ZoomMode.FitToWidth)
        # 「适应宽度」时滚动条一出现可用宽度就变小，重新适应后又不需要滚动条：来回震荡。
        # 让预览里的滚动条常驻，宽度就固定了。
        for view in self.preview_widget.findChildren(QGraphicsView):
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.preview_widget.setAccessibleName("report-preview")
        self.preview_widget.paintRequested.connect(self._paint_preview)
        self.preview_widget.previewChanged.connect(self._update_pager)
        layout.addWidget(self.preview_widget, 1)
        pager = QHBoxLayout()
        pager.setSpacing(8)
        self.prev_button = Button("", ButtonVariant.SECONDARY, icon="chevron-down")
        self.next_button = Button("", ButtonVariant.SECONDARY, icon="chevron-down")
        self.prev_button.setFixedWidth(60)
        self.next_button.setFixedWidth(60)
        self.page_label = Label("", "meta", mono=True)
        pager.addStretch(1)
        pager.addWidget(self.prev_button)
        pager.addWidget(self.page_label)
        pager.addWidget(self.next_button)
        pager.addStretch(1)
        layout.addLayout(pager)
        self.prev_button.clicked.connect(lambda: self._turn(-1))
        self.next_button.clicked.connect(lambda: self._turn(1))
        return self.desk

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)

        # 签名状态
        self.status_card = Card(" ")
        self.status_banner = QFrame()
        self.status_banner.setProperty("role", "banner")
        banner = QHBoxLayout(self.status_banner)
        banner.setContentsMargins(12, 10, 12, 10)
        banner.setSpacing(10)
        self.status_icon = Label()
        self.status_text = Label("", "body")
        self.status_text.setWordWrap(True)
        banner.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        banner.addWidget(self.status_text, 1)
        self.status_card.body.addWidget(self.status_banner)
        self.reasons_label = Label("", "body")
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setVisible(False)
        self.status_card.body.addWidget(self.reasons_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._kv_labels: dict[str, Label] = {}
        self._kv_values: dict[str, Label] = {}
        for index, key in enumerate(("signer", "signed_at", "certificate", "file", "language")):
            name = Label("", "meta")
            value = Label("", "body", mono=key in {"signed_at", "file"})
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, index, 1)
            self._kv_labels[key] = name
            self._kv_values[key] = value
        self.status_card.body.addLayout(grid)
        self.verify_button = Button("", ButtonVariant.SECONDARY, icon="shield-check")
        self.verify_file_button = Button("", ButtonVariant.SECONDARY)
        self.verify_result = Label("", "body")
        self.verify_result.setWordWrap(True)
        self.verify_result.setVisible(False)
        self.notice = Label("", "meta")
        self.notice.setWordWrap(True)
        for widget in (self.verify_button, self.verify_file_button):
            self.status_card.body.addWidget(widget)
        self.status_card.body.addWidget(self.verify_result)
        self.status_card.body.addWidget(self.notice)
        column.addWidget(self.status_card)

        # 签署和导出
        self.actions_card = Card(" ")
        self.language_label = Label("", "lab")
        self.language_box = QComboBox()
        for language in ReportLanguage:
            self.language_box.addItem(_LANGUAGE_NAMES[language], language.value)
        self.language_note = Label("", "meta")
        self.language_note.setWordWrap(True)
        self.sign_button = Button("", ButtonVariant.PRIMARY, icon="key-round")
        self.sign_hint = Label("", "meta")
        self.sign_hint.setWordWrap(True)
        self.print_button = Button("", ButtonVariant.SECONDARY, icon="printer")
        self.export_button = Button("", ButtonVariant.SECONDARY)
        self.batch_export_button = Button("", ButtonVariant.SECONDARY)
        for action_widget in (
            self.language_label,
            self.language_box,
            self.language_note,
            self.sign_button,
            self.sign_hint,
            self.print_button,
            self.export_button,
            self.batch_export_button,
        ):
            self.actions_card.body.addWidget(action_widget)
        column.addWidget(self.actions_card)

        # 单批次报告
        self.batch_card = Card(" ")
        self.batch_hint = Label("", "meta")
        self.batch_hint.setWordWrap(True)
        self.batch_kind_box = QComboBox()
        self.batch_kind_box.addItem("", BatchKind.LAL_REAGENT.value)
        self.batch_kind_box.addItem("", BatchKind.SAMPLE.value)
        self.batch_box = QComboBox()
        self.batch_button = Button("", ButtonVariant.SECONDARY)
        self.batch_empty = Label("", "warn")
        self.batch_empty.setWordWrap(True)
        self.batch_empty.setVisible(False)
        self.batch_back = Button("", ButtonVariant.SECONDARY)
        self.batch_back.setVisible(False)
        for item_widget in (
            self.batch_hint,
            self.batch_kind_box,
            self.batch_box,
            self.batch_button,
            self.batch_empty,
            self.batch_back,
        ):
            self.batch_card.body.addWidget(item_widget)
        column.addWidget(self.batch_card)
        column.addStretch(1)

        for wrapped in panel.findChildren(Label):
            if wrapped.wordWrap():
                # 自动换行的文字要按宽度算高度，不能被「只取需要的高度」截断。
                wrapped.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.language_box.currentIndexChanged.connect(self._on_language_changed)
        self.sign_button.clicked.connect(self.request_sign)
        self.verify_button.clicked.connect(self.verify_current)
        self.verify_file_button.clicked.connect(self.verify_external)
        self.print_button.clicked.connect(self.request_print)
        self.export_button.clicked.connect(self.export_current)
        self.batch_export_button.clicked.connect(self.request_batch_export)
        self.batch_kind_box.currentIndexChanged.connect(self._fill_batches)
        self.batch_button.clicked.connect(self.show_selected_batch)
        self.batch_back.clicked.connect(self._back_to_experiment)
        return panel

    # ------------------------------------------------------------------ 公开接口

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    def set_ui_language(self, code: str) -> None:
        """界面语言变了：用户没有手动选过报告语言时，报告跟随界面语言。

        Args:
            code: 语言代码（``zh_CN`` 或 ``en``）。
        """
        self._ui_language = report_language_of(code)
        if not self._language_chosen:
            self._set_language_box(self._ui_language)
            if self._preview is not None and not self._preview.document.signed:
                self._reload_preview()

    @property
    def current_preview(self) -> ReportPreview | None:
        """当前预览，没有时为 None。"""
        return self._preview

    @property
    def experiment_id(self) -> str | None:
        """当前显示的试验标识；单批次报告或空状态为 None。"""
        return self._preview.experiment_id if self._preview is not None else None

    @property
    def current_record(self) -> ReportRecord | None:
        """当前报告已入库的签署记录。"""
        return self._record

    @property
    def last_verification(self) -> SignatureVerification | None:
        """最近一次验签的结果。"""
        return None if self._verification is None else self._verification[0]

    def refresh(self) -> None:
        """重新读取可出报告的试验，保持当前选择。"""
        keep = self.experiment_id
        self._experiments = []
        if self._session is not None:
            try:
                self._experiments = self._services.reports.report_experiments(
                    self._session, MAX_EXPERIMENTS
                )
            except (AuthError, ServiceError):
                self._experiments = []
        self._loading = True
        try:
            self.experiment_box.clear()
            for item in self._experiments:
                self.experiment_box.addItem(self._item_text(item), item.experiment_id)
        finally:
            self._loading = False
        self._fill_batches()
        if not self._experiments and (keep is None or self._batch is None):
            self._preview = None
            self._record = None
            self._show_empty(True)
            return
        self._show_empty(False)
        target = keep or self._experiments[0].experiment_id
        if self._batch is not None and keep is None:
            self._show_batch(*self._batch)
        else:
            self.open_experiment(target)

    def open_experiment(self, experiment_id: str) -> bool:
        """显示某个试验的报告（试验不在列表里时临时加上，例如还没完成的试验）。

        Args:
            experiment_id: 试验标识。

        Returns:
            成功显示时为 True。
        """
        if self._session is None:
            return False
        self._batch = None
        index = self.experiment_box.findData(experiment_id)
        self._loading = True
        try:
            if index < 0:
                self.experiment_box.insertItem(0, experiment_id, experiment_id)
                index = 0
            self.experiment_box.setCurrentIndex(index)
        finally:
            self._loading = False
        self._show_empty(False)
        return self._load_experiment(experiment_id)

    def open_batch(self, kind: BatchKind, batch_no: str) -> bool:
        """显示某个批号的单批次报告。

        Args:
            kind: 批号种类。
            batch_no: 批号。

        Returns:
            成功显示时为 True。
        """
        if self._session is None:
            return False
        self._fill_batches()
        self.batch_kind_box.setCurrentIndex(self.batch_kind_box.findData(kind.value))
        self._fill_batches()
        found = self.batch_box.findData(batch_no)
        if found >= 0:
            self.batch_box.setCurrentIndex(found)
        self._show_empty(False)
        return self._show_batch(kind, batch_no)

    # ------------------------------------------------------------------ 读取和显示

    def _item_text(self, item: ExperimentSummary) -> str:
        return (
            f"{item.experiment_id} · {experiment_type_label(item.experiment_type)} · "
            f"{experiment_state_label(item.state)}"
        )

    def _on_experiment_changed(self, index: int) -> None:
        if self._loading or index < 0:
            return
        experiment_id = self.experiment_box.itemData(index)
        if experiment_id:
            self._batch = None
            self._load_experiment(str(experiment_id))

    def _chosen_language(self) -> ReportLanguage:
        return report_language_of(str(self.language_box.currentData()))

    def _load_experiment(self, experiment_id: str) -> bool:
        if self._session is None:
            return False
        try:
            self._preview = self._services.reports.preview(
                self._session, experiment_id, self._chosen_language()
            )
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return False
        except StorageBusyError:
            self._toast(describe_storage_busy(), ToastKind.WARNING)
            return False
        self._record = self._preview.record
        self._verification = None
        self._after_preview()
        return True

    def _show_batch(self, kind: BatchKind, batch_no: str) -> bool:
        if self._session is None:
            return False
        try:
            self._preview = self._services.reports.batch_preview(
                self._session, kind, batch_no, self._chosen_language()
            )
            records = self._services.reports.batch_reports(self._session, kind, batch_no)
        except (AuthError, ServiceError) as error:
            self._preview = None
            self.batch_empty.setText(self._error_text(error))
            self.batch_empty.setVisible(True)
            self._render()
            return False
        self._batch = (kind, batch_no)
        self._record = records[-1] if records else None
        self._verification = None
        self.batch_empty.setVisible(False)
        self._after_preview()
        return True

    def _reload_preview(self) -> None:
        if self._batch is not None:
            self._show_batch(*self._batch)
        elif self.experiment_id is not None:
            self._load_experiment(self.experiment_id)

    def _after_preview(self) -> None:
        self._set_language_for_preview()
        self._render()
        self.preview_widget.updatePreview()

    def _paint_preview(self, printer: QPrinter) -> None:
        preview = self._preview
        if preview is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._services.reports.paint(preview, printer)
        finally:
            QApplication.restoreOverrideCursor()

    def _turn(self, step: int) -> None:
        page = self.preview_widget.currentPage() + step
        if 1 <= page <= self.preview_widget.pageCount():
            self.preview_widget.setCurrentPage(page)

    def _update_pager(self) -> None:
        total = self.preview_widget.pageCount()
        current = min(max(1, self.preview_widget.currentPage()), max(1, total))
        self.page_label.setText(self.tr("第 {page} / {total} 页").format(page=current, total=total))
        self.prev_button.setEnabled(current > 1)
        self.next_button.setEnabled(current < total)

    def _show_empty(self, empty: bool) -> None:
        self.empty_card.setVisible(empty)
        self.content.setVisible(not empty)
        self.experiment_box.setVisible(not empty)
        self.experiment_label.setVisible(not empty)
        self.refresh_button.setVisible(not empty)

    # ------------------------------------------------------------------ 渲染右侧状态

    def _can(self, permission: Permission) -> bool:
        return self._session is not None and self._session.can(permission)

    def _set_language_box(self, language: ReportLanguage) -> None:
        self.language_box.blockSignals(True)
        self.language_box.setCurrentIndex(self.language_box.findData(language.value))
        self.language_box.blockSignals(False)

    def _set_language_for_preview(self) -> None:
        """已签署的报告语言固定为签署时的语言，下拉框显示它并禁用。"""
        preview = self._preview
        if preview is None:
            return
        self._set_language_box(preview.language)
        self.language_box.setEnabled(not preview.document.signed)

    def _on_language_changed(self, _index: int) -> None:
        self._language_chosen = True
        self._reload_preview()

    def _render(self) -> None:
        preview = self._preview
        batch = preview is not None and preview.scope is ReportScope.BATCH
        self.batch_back.setVisible(batch)
        if preview is None:
            self.meta.setText("")
            self.sign_button.setEnabled(False)
            self.print_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.verify_button.setEnabled(False)
            return
        if batch:
            kind, number = self._batch or (BatchKind.LAL_REAGENT, "")
            kind_name = self._kind_name(kind)
            self.meta.setText(f"{kind_name} {number}")
        else:
            section = preview.document.sections[0]
            self.meta.setText(
                f"{section.experiment_id} · {experiment_type_label(section.experiment_type)}"
            )
        self._render_status(preview, batch)
        self._render_actions(preview, batch)
        self._update_pager()

    def _tone(self, tone: str, text: str) -> None:
        self.status_banner.setProperty("tone", tone)
        self.status_banner.style().unpolish(self.status_banner)
        self.status_banner.style().polish(self.status_banner)
        icon, color = {
            "success": ("shield-check", "success"),
            "warning": ("triangle-alert", "warning"),
            "danger": ("triangle-alert", "danger"),
            "info": ("info", "primary"),
        }[tone]
        self.status_icon.setPixmap(IconLoader.pixmap(icon, 22, color))
        self.status_text.setText(text)

    def _render_status(self, preview: ReportPreview, batch: bool) -> None:
        record = self._record
        signed = record is not None
        verdict_text = ""
        if preview.conclusion is not None:
            verdict_text = conclusion_verdict_label(preview.conclusion.verdict)
        reasons: list[str] = []
        if signed:
            self._tone("success", self.tr("已签署"))
        elif preview.can_sign:
            self._tone(
                "info",
                self.tr("可以签署：试验结论为「{verdict}」").format(verdict=verdict_text)
                if not batch
                else self.tr("可以签署：批次里的试验都已签署"),
            )
        else:
            reasons = describe_signing_blocks(preview.blocks, preview.conclusion, batch=batch)
            danger = any(
                b.blocker in (SigningBlocker.VERDICT_INVALID, SigningBlocker.VERDICT_PENDING)
                for b in preview.blocks
            )
            self._tone("danger" if danger else "warning", self.tr("现在不能签署"))
        self.reasons_label.setText("\n".join(reasons))
        self.reasons_label.setVisible(bool(reasons))
        who = None if record is None else record.signed_by
        if record is not None and record.signed_by is not None:
            who = self._display_name(record.signed_by)
        self._kv_values["signer"].setText(who or "–")
        self._kv_values["signed_at"].setText(_stamp(None if record is None else record.signed_at))
        certificate = self._services.reports.certificate_info(self._session) if signed else None
        if record is not None and record.certificate_sha256:
            subject = certificate[0] if certificate is not None else ""
            self._kv_values["certificate"].setText(
                f"{subject}\n{_short(record.certificate_sha256)}".strip()
            )
        else:
            self._kv_values["certificate"].setText("–")
        self._kv_values["file"].setText(
            "–" if record is None else f"{Path(record.path).name}\n{_short(record.sha256)}"
        )
        self._kv_values["language"].setText(
            "–" if record is None else _LANGUAGE_NAMES[report_language_of(record.language)]
        )
        self.verify_button.setEnabled(signed)
        self._render_verification()

    def _display_name(self, username: str) -> str:
        """签署人显示名：报告预览里已经解析过，直接取。"""
        preview = self._preview
        if preview is not None and preview.document.signed_by is not None:
            return preview.document.signed_by
        if preview is not None:
            for section in preview.document.sections:
                if section.signer_name:
                    return section.signer_name
        return username

    def _render_verification(self) -> None:
        if self._verification is None:
            self.verify_result.setVisible(False)
            return
        result, name, stored = self._verification
        lines = describe_verification(result, stored=stored)
        prefix = "" if stored else f"{name}\n"
        self.verify_result.set_role("ok" if result.ok else "danger")
        self.verify_result.setText(prefix + "\n".join(lines))
        self.verify_result.setVisible(True)

    def _render_actions(self, preview: ReportPreview, batch: bool) -> None:
        can_sign_role = self._can(Permission.EXPERIMENT_SIGN)
        signed = self._record is not None
        self.sign_button.setEnabled(preview.can_sign and can_sign_role and not signed)
        if not can_sign_role:
            self.sign_hint.setText(self.tr("当前角色没有签署权限，签署需要分析员。"))
        elif signed:
            self.sign_hint.setText(self.tr("已经签署，可以重新验签或导出同一份文件。"))
        else:
            self.sign_hint.setText(
                self.tr("签署需要再次输入密码；签署后结果和复核被锁定。")
                if not batch
                else self.tr("单批次报告汇总已签署的试验，不改变试验状态。")
            )
        self.print_button.setEnabled(True)
        can_export = self._can(Permission.DATA_EXPORT)
        self.export_button.setEnabled(signed and can_export)
        has_signed = False
        if can_export and self._session is not None:
            try:
                has_signed = bool(self._services.reports.signed_reports(self._session, limit=1))
            except (AuthError, ServiceError):
                has_signed = False
        self.batch_export_button.setEnabled(has_signed and can_export)
        self.language_note.setText(
            self.tr("已签署的报告语言固定为签署时的语言。")
            if preview.document.signed
            else self.tr("未签署时可以选择报告语言，签署时按所选语言生成。")
        )

    # ------------------------------------------------------------------ 批次

    def _kind_name(self, kind: BatchKind) -> str:
        return self.tr("鲎试剂批号") if kind is BatchKind.LAL_REAGENT else self.tr("样品批号")

    def _selected_kind(self) -> BatchKind:
        return BatchKind(str(self.batch_kind_box.currentData()))

    def _fill_batches(self) -> None:
        self.batch_box.clear()
        numbers: list[str] = []
        if self._session is not None:
            try:
                numbers = self._services.reports.batch_numbers(self._session, self._selected_kind())
            except (AuthError, ServiceError):
                numbers = []
        for number in numbers:
            self.batch_box.addItem(number, number)
        self.batch_button.setEnabled(bool(numbers))
        self.batch_empty.setText(self.tr("还没有已完成的试验，没有可汇总的批次。"))
        self.batch_empty.setVisible(not numbers)

    def show_selected_batch(self) -> None:
        """预览下拉框里选中的批号的单批次报告。"""
        number = self.batch_box.currentData()
        if number:
            self._show_batch(self._selected_kind(), str(number))

    def _back_to_experiment(self) -> None:
        self._batch = None
        target = self.experiment_box.currentData()
        if target:
            self._load_experiment(str(target))

    # ------------------------------------------------------------------ 操作

    def request_sign(self) -> None:
        """签署当前报告：弹出对话框，再次输入密码。"""
        preview = self._preview
        session = self._session
        if preview is None or session is None or not preview.can_sign:
            return
        batch = preview.scope is ReportScope.BATCH
        subject = (
            f"{self._kind_name(self._batch[0])} {self._batch[1]}"
            if batch and self._batch is not None
            else str(preview.experiment_id)
        )
        language = self._chosen_language()
        outcome: list[SignedReport] = []

        def apply(password: str) -> None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                if batch and self._batch is not None:
                    kind, number = self._batch
                    outcome.append(
                        self._services.reports.sign_batch(session, kind, number, password, language)
                    )
                else:
                    outcome.append(
                        self._services.reports.sign(
                            session, str(preview.experiment_id), password, language
                        )
                    )
            finally:
                QApplication.restoreOverrideCursor()

        dialog = SignDialog(apply, subject=subject, signer=session.user.display_name, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not outcome:
            return
        self._toast(
            self.tr("已签署，报告已保存：{name}").format(name=outcome[0].path.name),
            ToastKind.SUCCESS,
        )
        record = outcome[0].record
        self.refresh()
        if batch and self._batch is not None:
            self.open_batch(*self._batch)
        elif record.experiment_id is not None:
            self.open_experiment(record.experiment_id)
            self.signedExperiment.emit(record.experiment_id)

    def verify_current(self) -> None:
        """重新验签当前报告：签名、证书、是否覆盖整个文件、文件哈希。"""
        record = self._record
        if record is None or self._session is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self._services.reports.verify_report(self._session, record.report_id)
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._verification = (result, Path(record.path).name, True)
        self._render_verification()

    def verify_external(self) -> None:
        """选一个外部的 PDF 文件验签。"""
        if self._session is None:
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择要验证的 PDF"), "", self.tr("PDF 文件 (*.pdf)")
        )
        if not chosen:
            return
        try:
            result = self._services.reports.verify_file(self._session, Path(chosen))
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return
        self._verification = (result, Path(chosen).name, False)
        self._render_verification()

    def request_print(self) -> None:
        """打印当前报告：弹出系统打印对话框。"""
        if self._preview is None:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_printer(printer)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.print_to(printer)

    def print_to(self, printer: QPrinter) -> int:
        """把当前报告画到打印机上，并写打印审计。

        Args:
            printer: 打印机（也可以是输出到 PDF 的 ``QPrinter``）。

        Returns:
            页数；没有可打印的报告时为 0。
        """
        preview = self._preview
        if preview is None or self._session is None:
            return 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pages = self._services.reports.paint(preview, printer)
            self._services.reports.record_print(self._session, preview)
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return 0
        finally:
            QApplication.restoreOverrideCursor()
        self._toast(self.tr("已发送到打印机（{pages} 页）").format(pages=pages), ToastKind.SUCCESS)
        return pages

    def export_current(self) -> None:
        """把当前已签署的 PDF 另存为一个文件（同一份签名文件）。"""
        record = self._record
        if record is None or self._session is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出已签名 PDF"),
            Path(record.path).name,
            self.tr("PDF 文件 (*.pdf)"),
        )
        if not target:
            return
        try:
            written = self._services.reports.export_report_file(
                self._session, record.report_id, Path(target)
            )
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return
        self._toast(self.tr("已导出：{name}").format(name=written.name), ToastKind.SUCCESS)

    def request_batch_export(self) -> None:
        """打开批量导出对话框。"""
        session = self._session
        if session is None:
            return
        try:
            records = self._services.reports.signed_reports(session)
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return

        def export(ids: Sequence[str], directory: str) -> ExportResult:
            return self._services.reports.export_reports(session, ids, Path(directory))

        BatchExportDialog(records, export, parent=self).exec()

    # ------------------------------------------------------------------ 提示

    def _error_text(self, error: Exception) -> str:
        if isinstance(error, ServiceError):
            return describe_service_error(error)
        return str(error)

    def _fail(self, error: Exception) -> None:
        _log.info("报告中心操作失败：%s", error)
        self._toast(self._error_text(error), ToastKind.WARNING)

    def _toast(self, text: str, kind: ToastKind) -> None:
        Toast.show_message(self.window(), text, kind)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("报告中心"))
        self.experiment_label.setText(self.tr("试验"))
        self.refresh_button.setText(self.tr("刷新"))
        self.empty_title.setText(self.tr("还没有可出报告的试验"))
        self.empty_hint.setText(
            self.tr(
                "试验完成并复核后，在这里预览、打印和签署报告。可以先到历史记录里查看已有的试验。"
            )
        )
        self.empty_button.setText(self.tr("打开历史记录"))
        self.status_card.set_title(self.tr("签名状态"))
        for key, text in (
            ("signer", self.tr("签署人")),
            ("signed_at", self.tr("签署时间")),
            ("certificate", self.tr("证书")),
            ("file", self.tr("文件")),
            ("language", self.tr("语言")),
        ):
            self._kv_labels[key].setText(text)
        self.verify_button.setText(self.tr("重新验签"))
        self.verify_file_button.setText(self.tr("验证外部 PDF…"))
        self.notice.setText(self_signed_notice())
        self.actions_card.set_title(self.tr("签署和导出"))
        self.language_label.setText(self.tr("报告语言"))
        self.sign_button.setText(self.tr("签署并生成 PDF"))
        self.print_button.setText(self.tr("打印"))
        self.export_button.setText(self.tr("导出已签名 PDF"))
        self.batch_export_button.setText(self.tr("批量导出…"))
        self.batch_card.set_title(self.tr("单批次报告"))
        self.batch_hint.setText(self.tr("把同一批号的多次试验汇总到一份报告。"))
        self.batch_kind_box.setItemText(0, self.tr("鲎试剂批号"))
        self.batch_kind_box.setItemText(1, self.tr("样品批号"))
        self.batch_button.setText(self.tr("预览单批次报告"))
        self.batch_back.setText(self.tr("返回试验报告"))
        for index in range(self.experiment_box.count()):
            experiment_id = self.experiment_box.itemData(index)
            match = next((e for e in self._experiments if e.experiment_id == experiment_id), None)
            if match is not None:
                self.experiment_box.setItemText(index, self._item_text(match))
        self._update_pager()
        if self._preview is not None:
            self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


__all__ = ["ReportsPage"]
