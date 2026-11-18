"""报告中心：实验报告列表、独立报告预览和导出。

页面不碰数据库和文件：预览、签署、验签、导出都走 ``ReportService``，权限由服务层再校验一遍。
预览用 ``QPrintPreviewWidget``，画的是和 PDF 同一套排版（``ReportService.paint``）。

* 单个试验的报告：按当前界面语言预览和签署、签署（再次输入密码）、
  重新验签、打印、导出已签名 PDF。
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

from gelinspect.domain.permissions import Permission
from gelinspect.domain.signing import SigningBlocker
from gelinspect.interfaces.storage import ExperimentSummary, ReportRecord, StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.export_service import suggested_filename
from gelinspect.services.reporting.renderer import configure_printer
from gelinspect.services.reporting.report_service import (
    ExportResult,
    ReportListItem,
    ReportPreview,
    SignedReport,
)
from gelinspect.services.reporting.texts import ReportLanguage, report_language_of
from gelinspect.services.reporting.verify import SignatureVerification
from gelinspect.ui.export_labels import export_labels
from gelinspect.ui.labels import (
    conclusion_verdict_label,
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    experiment_state_label,
    experiment_type_label,
)
from gelinspect.ui.pages.export_password import PasswordProvider, ask_export_password
from gelinspect.ui.pages.report_dialogs import BatchExportDialog, SignDialog
from gelinspect.ui.punctuation import join_items
from gelinspect.ui.report_labels import (
    describe_signing_blocks,
    describe_verification,
    self_signed_notice,
)
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)

MAX_EXPERIMENTS = 200
"""单个实验快捷下拉框最多列出的试验数。报告中心列表单独全量查询。"""
PANEL_WIDTH = 330
"""右侧操作栏宽度。"""
_LANGUAGE_NAMES = {
    ReportLanguage.ZH_CN: "简体中文",
    ReportLanguage.ZH_TW: "繁體中文",
    ReportLanguage.EN: "English",
}
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
    experimentDataRequested = Signal(str)  # noqa: N815
    navigationRequested = Signal(str)  # noqa: N815

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
        self._experiments: list[ExperimentSummary] = []
        self._report_items: list[ReportListItem] = []
        self._list_mode = True
        self._import_verification: object | None = None
        self._preview: ReportPreview | None = None
        self._ui_language = ReportLanguage.ZH_CN
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
        self._build_report_list()
        self._build_content()
        self._column.addStretch(1)
        self.retranslate()
        self._show_empty(True)

    def _build_report_list(self) -> None:
        """首页实验列表、批号筛选、批量导出和外部 PDF 导入。"""
        self.list_card = Card()
        column = QVBoxLayout()
        column.setSpacing(10)
        controls = QHBoxLayout()
        self.sample_filter = QComboBox()
        self.lal_filter = QComboBox()
        self.buffer_filter = QComboBox()
        self.endotoxin_filter = QComboBox()
        self.list_refresh = Button("", ButtonVariant.SECONDARY, icon="rotate-ccw")
        self.list_export = Button("", ButtonVariant.PRIMARY)
        self.import_pdf_button = Button("", ButtonVariant.SECONDARY)
        for widget in (
            self.sample_filter,
            self.lal_filter,
            self.buffer_filter,
            self.endotoxin_filter,
        ):
            widget.setMinimumWidth(130)
            controls.addWidget(widget, 1)
        controls.addWidget(self.list_refresh)
        controls.addWidget(self.list_export)
        controls.addWidget(self.import_pdf_button)
        column.addLayout(controls)
        self.report_table = DataTable(
            [
                Column("experiment_id", "实验", mono=True),
                Column("created_at", "时间", mono=True),
                Column("sample_batches", "样品批号", mono=True),
                Column("reagent_batches", "试剂批号", mono=True),
                Column("report_state", "报告状态"),
            ],
            empty_title="没有匹配的实验报告",
        )
        self.report_table.set_multi_select(True)
        self.report_table.setMinimumHeight(400)
        column.addWidget(self.report_table, 1)
        result_row = QHBoxLayout()
        self.import_result = Label("", "body")
        self.import_result.setWordWrap(True)
        self.import_open = Button("", ButtonVariant.SECONDARY)
        self.import_open.setVisible(False)
        result_row.addWidget(self.import_result, 1)
        result_row.addWidget(self.import_open)
        column.addLayout(result_row)
        self.list_card.body.addLayout(column)
        self._column.addWidget(self.list_card)
        self.list_refresh.clicked.connect(self.refresh)
        self.list_export.clicked.connect(self.request_batch_export)
        self.import_pdf_button.clicked.connect(self.import_external_pdf)
        self.import_open.clicked.connect(self.open_imported_match)
        self.report_table.rowActivated.connect(self._open_list_row)
        for box in (self.sample_filter, self.lal_filter, self.buffer_filter, self.endotoxin_filter):
            box.currentIndexChanged.connect(self._apply_report_filters)

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
        self.back_to_list = Button("", ButtonVariant.SECONDARY)
        self.back_to_list.setVisible(False)
        top.addWidget(self.back_to_list)
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
        self.back_to_list.clicked.connect(self.show_report_list)

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
        # “适应宽度”时滚动条一出现可用宽度就变小，重新适应后又不需要滚动条：来回震荡。
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
        self.sign_button = Button("", ButtonVariant.PRIMARY, icon="key-round")
        self.sign_hint = Label("", "meta")
        self.sign_hint.setWordWrap(True)
        self.print_button = Button("", ButtonVariant.SECONDARY, icon="printer")
        self.export_button = Button("", ButtonVariant.SECONDARY)
        self.excel_button = Button("", ButtonVariant.SECONDARY)
        self.experiment_data_button = Button("", ButtonVariant.SECONDARY)
        for action_widget in (
            self.sign_button,
            self.sign_hint,
            self.print_button,
            self.export_button,
            self.excel_button,
            self.experiment_data_button,
        ):
            self.actions_card.body.addWidget(action_widget)
        column.addWidget(self.actions_card)

        column.addStretch(1)

        for wrapped in panel.findChildren(Label):
            if wrapped.wordWrap():
                # 自动换行的文字要按宽度算高度，不能被“只取需要的高度”截断。
                wrapped.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.sign_button.clicked.connect(self.request_sign)
        self.verify_button.clicked.connect(self.verify_current)
        self.verify_file_button.clicked.connect(self.verify_external)
        self.print_button.clicked.connect(self.request_print)
        self.export_button.clicked.connect(self.export_current)
        self.excel_button.clicked.connect(self.export_excel)
        self.experiment_data_button.clicked.connect(self.open_experiment_data)
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
        """记录当前界面语言，报告操作使用该语言快照。

        Args:
            code: 语言代码（``zh_CN``、``zh_TW`` 或 ``en``）。
        """
        language = report_language_of(code)
        if language != self._ui_language:
            self._ui_language = language
            if self.experiment_id is not None:
                self._reload_preview()

    @property
    def current_preview(self) -> ReportPreview | None:
        """当前预览，没有时为 None。"""
        return self._preview

    @property
    def experiment_id(self) -> str | None:
        """当前显示的实验标识；空状态时为 None。"""
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
        try:
            self._report_items = (
                list(self._services.reports.report_list(self._session))
                if self._session is not None
                else []
            )
        except (AuthError, ServiceError):
            self._report_items = []
        self._loading = True
        try:
            self.experiment_box.clear()
            for item in self._experiments:
                self.experiment_box.addItem(self._item_text(item), item.experiment_id)
        finally:
            self._loading = False
        self._refresh_report_list()
        if self._list_mode:
            self.show_report_list()
            return
        if not self._experiments:
            self._preview = None
            self._record = None
            self._show_empty(True)
            return
        self._show_empty(False)
        target = keep or self._experiments[0].experiment_id
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
        self._list_mode = False
        self.list_card.setVisible(False)
        self.back_to_list.setVisible(True)
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

    def show_report_list(self) -> None:
        """返回报告列表并保留当前筛选。"""
        self._list_mode = True
        self.list_card.setVisible(True)
        self.content.setVisible(False)
        self.header.setVisible(False)
        self.empty_card.setVisible(False)
        self.back_to_list.setVisible(False)

    def _refresh_report_list(self) -> None:
        sample_values = sorted(
            {value for item in self._report_items for value in item.sample_batch_nos}
        )
        for box, values, label in (
            (self.sample_filter, sample_values, self.tr("全部样品批号")),
            (
                self.lal_filter,
                sorted({item.lal_batch_no for item in self._report_items if item.lal_batch_no}),
                self.tr("全部鲎试剂批号"),
            ),
            (
                self.buffer_filter,
                sorted({item.water_batch_no for item in self._report_items if item.water_batch_no}),
                self.tr("全部缓冲液批号"),
            ),
            (
                self.endotoxin_filter,
                sorted(
                    item.endotoxin_batch_no
                    for item in self._report_items
                    if item.endotoxin_batch_no
                ),
                self.tr("全部内毒素试剂批号"),
            ),
        ):
            keep = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem(label, "")
            for value in values:
                box.addItem(value, value)
            index = box.findData(keep)
            box.setCurrentIndex(max(index, 0))
            box.blockSignals(False)
        self._apply_report_filters()

    def _apply_report_filters(self, _index: int = -1) -> None:
        selected_ids = {
            self._visible_report_items[row].experiment_id
            for row in self.report_table.selected_rows()
            if 0 <= row < len(self._visible_report_items)
        }
        rows: list[dict[str, str]] = []
        self._visible_report_items: list[ReportListItem] = []
        sample = str(self.sample_filter.currentData() or "")
        for item in self._report_items:
            samples = item.sample_batch_nos
            reagents = (
                item.lal_batch_no or "",
                item.water_batch_no or "",
                item.endotoxin_batch_no or "",
            )
            if sample and sample not in samples:
                continue
            filters = (self.lal_filter, self.buffer_filter, self.endotoxin_filter)
            if any(
                box.currentData() and str(box.currentData()) != actual
                for box, actual in zip(filters, reagents, strict=True)
            ):
                continue
            record = getattr(item, "latest_report", None)
            rows.append(
                {
                    "experiment_id": item.experiment_id,
                    "created_at": item.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                    "sample_batches": join_items(samples),
                    "reagent_batches": join_items(value for value in reagents if value),
                    "report_state": self.tr("已签署") if record is not None else self.tr("待签署"),
                }
            )
            self._visible_report_items.append(item)
        self.report_table.set_rows(rows)
        for row, item in enumerate(self._visible_report_items):
            if item.experiment_id in selected_ids:
                self.report_table.view.selectRow(row)
        self.list_export.setEnabled(bool(rows) and self._can(Permission.DATA_EXPORT))

    def _open_list_row(self, row: int) -> None:
        if 0 <= row < len(self._visible_report_items):
            self.open_experiment(str(self._visible_report_items[row].experiment_id))

    def import_external_pdf(self) -> None:
        """校验外部 PDF，并显示其签署状态及本库匹配的实验。"""
        if self._session is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("导入外部 PDF"), "", self.tr("PDF 文件 (*.pdf)")
        )
        if not filename:
            return
        try:
            result = self._services.reports.verify_imported_file(self._session, Path(filename))
        except (AuthError, ServiceError) as error:
            self.import_result.setText(self._error_text(error))
            self.import_open.setVisible(False)
            return
        self._import_verification = result
        status = str(result.signature_status)
        status_text = {
            "valid": self.tr("数字签名有效，报告内容和签署记录已匹配。"),
            "invalid": self.tr("数字签名无效，文件不能作为可信报告。"),
            "unsigned": self.tr("PDF 未签名。"),
            "unknown": self.tr("无法识别签名或签署者，请检查文件权限并联系后台检查。"),
            "corrupt": self.tr("PDF 损坏或无法读取，请检查文件权限并联系后台检查。"),
            "external": self.tr("数字签名有效，但不是本系统已知报告。"),
        }.get(status, self.tr("无法确认 PDF 状态，请检查文件权限并联系后台检查。"))
        self.import_result.setText(f"{status_text}\n{result.message}")
        self.import_open.setVisible(bool(result.experiment_id and status == "valid"))

    def open_imported_match(self) -> None:
        """打开有效签名匹配到的实验报告。"""
        result = self._import_verification
        experiment_id = None if result is None else getattr(result, "experiment_id", None)
        if experiment_id:
            self.open_experiment(str(experiment_id))

    def open_experiment_data(self) -> None:
        """请求打开当前报告对应的实验数据。"""
        experiment_id = self.experiment_id
        if experiment_id:
            self.experimentDataRequested.emit(experiment_id)

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
            self._load_experiment(str(experiment_id))

    def _load_experiment(self, experiment_id: str) -> bool:
        if self._session is None:
            return False
        try:
            self._preview = self._services.reports.preview(
                self._session, experiment_id, self._ui_language
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

    def _reload_preview(self) -> None:
        if self.experiment_id is not None:
            self._load_experiment(self.experiment_id)

    def _after_preview(self) -> None:
        self._render()
        self.preview_widget.updatePreview()

    def _paint_preview(self, printer: QPrinter) -> None:
        preview = self._preview
        if preview is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._services.reports.paint(self._session, preview, printer)
        except (AuthError, ServiceError) as error:
            self._clear_failed_preview(self._error_text(error))
            self._fail(error)
        except Exception:
            _log.exception("REPORT_PREVIEW_PAINT_FAILED")
            self._clear_failed_preview(self.tr("报告预览暂时无法显示，请点击“刷新预览”重试。"))
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
        self.content.setVisible(not empty and not self._list_mode)
        self.list_card.setVisible(not empty and self._list_mode)
        self.header.setVisible(not empty and not self._list_mode)
        self.experiment_box.setVisible(not empty and not self._list_mode)
        self.experiment_label.setVisible(not empty and not self._list_mode)
        self.refresh_button.setVisible(not empty and not self._list_mode)
        self.back_to_list.setVisible(not empty and not self._list_mode)

    # ------------------------------------------------------------------ 渲染右侧状态

    def _can(self, permission: Permission) -> bool:
        return self._session is not None and self._session.can(permission)

    def _render(self) -> None:
        preview = self._preview
        if preview is None:
            self.meta.setText("")
            self.sign_button.setEnabled(False)
            self.print_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.excel_button.setEnabled(False)
            self.experiment_data_button.setEnabled(False)
            self.verify_button.setEnabled(False)
            return
        section = preview.document.sections[0]
        self.meta.setText(
            f"{section.experiment_id} · {experiment_type_label(section.experiment_type)}"
        )
        self._render_status(preview)
        self._render_actions(preview)
        self.experiment_data_button.setEnabled(bool(preview.experiment_id))
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

    def _render_status(self, preview: ReportPreview) -> None:
        record = self._record
        signed = record is not None
        verdict_text = ""
        if preview.conclusion is not None:
            verdict_text = conclusion_verdict_label(preview.conclusion.verdict)
        reasons: list[str] = []
        if signed:
            self._tone("success", self.tr("已签署"))
        elif preview.can_sign:
            if preview.conclusions_enabled:
                text = self.tr("可以签署：实验结论为“{verdict}”").format(verdict=verdict_text)
            else:
                text = self.tr("可以签署：全部待复核已处理，实验已完成")
            self._tone("info", text)
        else:
            reasons = describe_signing_blocks(preview.blocks, preview.conclusion)
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

    def _render_actions(self, preview: ReportPreview) -> None:
        can_sign_role = self._can(Permission.EXPERIMENT_SIGN)
        signed = self._record is not None
        self.sign_button.setEnabled(preview.can_sign and can_sign_role and not signed)
        if not can_sign_role:
            self.sign_hint.setText(self.tr("当前账号没有报告签署权限，请联系管理员授权。"))
        elif signed:
            self.sign_hint.setText(self.tr("已经签署，可以重新验签或导出同一份文件。"))
        else:
            self.sign_hint.setText(
                self.tr("签署需要再次输入密码；后续复核将生成需重新签署的修订版。")
            )
        self.print_button.setEnabled(
            self._session is not None and self._session.can(Permission.REPORT_PRINT)
        )
        can_export = self._can(Permission.DATA_EXPORT)
        self.export_button.setEnabled(signed and can_export)
        self.excel_button.setEnabled(can_export)
        self.excel_button.setToolTip("" if can_export else self.tr("当前角色无此权限"))

    # ------------------------------------------------------------------ 操作

    def request_sign(self) -> None:
        """签署当前报告：弹出对话框，再次输入密码。"""
        preview = self._preview
        session = self._session
        if preview is None or session is None or not preview.can_sign:
            return
        subject = str(preview.experiment_id)
        # Snapshot language before the password dialog; subsequent UI changes
        # cannot silently change this signing operation.
        language = self._ui_language
        outcome: list[SignedReport] = []

        def apply(password: str) -> None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
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
        if record.experiment_id is not None:
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
            self._services.auth.authorize(self._session, Permission.REPORT_PRINT)
            pages = self._services.reports.paint(self._session, preview, printer)
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

    def export_excel(self) -> None:
        """选择文件并把当前试验的结果导出为 Excel（含结论、λc、单管特征和复核）。"""
        experiment_id = self.experiment_id
        if experiment_id is None or self._session is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出 Excel 数据"),
            suggested_filename(f"results-{experiment_id}"),
            self.tr("Excel 文件 (*.xlsx)"),
        )
        if target:
            self.export_excel_to(Path(target))

    def export_excel_to(self, destination: Path) -> bool:
        """把当前试验的结果导出到指定的 Excel 文件。

        Args:
            destination: 目标文件。

        Returns:
            导出成功时为 True。
        """
        experiment_id = self.experiment_id
        if experiment_id is None or self._session is None:
            return False
        password: str | None = None
        if self._services.exports.encryption_required:
            password = self.export_password_provider(self)
            if password is None:
                return False
        try:
            summary = self._services.exports.export_results(
                self._session, [experiment_id], destination, export_labels(), password=password
            )
        except (AuthError, ServiceError) as error:
            self._fail(error)
            return False
        self._toast(self.tr("已导出：{name}").format(name=summary.path.name), ToastKind.SUCCESS)
        return True

    def request_batch_export(self) -> None:
        """从列表中选择的实验打开批量导出对话框。"""
        session = self._session
        if session is None:
            return
        selected = self.report_table.selected_rows()
        if not selected:
            self._toast(self.tr("请先选择要导出的实验。"), ToastKind.WARNING)
            return
        selected_experiment_ids = {
            str(self._visible_report_items[row].experiment_id)
            for row in selected
            if 0 <= row < len(self._visible_report_items)
        }
        records = [
            item.latest_report
            for item in self._visible_report_items
            if item.experiment_id in selected_experiment_ids and item.latest_report is not None
        ]
        if not records:
            self._toast(self.tr("所选实验没有已签署报告可导出。"), ToastKind.WARNING)
            return

        def export(ids: Sequence[str], directory: str) -> ExportResult:
            password: str | None = None
            if self._services.exports.encryption_required:
                password = self.export_password_provider(self)
                if password is None:
                    raise ServiceError(
                        "已开启导出加密，必须设置导出密码",
                        ServiceErrorCode.EXPORT_PASSWORD_REQUIRED,
                    )
            return self._services.reports.export_reports(
                session, ids, Path(directory), password=password
            )

        BatchExportDialog(records, export, parent=self).exec()

    # ------------------------------------------------------------------ 提示

    def _error_text(self, error: Exception) -> str:
        if isinstance(error, AuthError):
            return describe_auth_error(error)
        if isinstance(error, ServiceError):
            return describe_service_error(error)
        return str(error)

    def _clear_failed_preview(self, message: str) -> None:
        """清除失效预览并在原位置说明原因。"""
        self._preview = None
        self.preview_widget.hide()
        self.status_text.setText(message)
        self.status_text.setVisible(True)
        self._update_pager()

    def _fail(self, error: Exception) -> None:
        _log.info("报告中心操作失败：%s", error)
        self._toast(self._error_text(error), ToastKind.WARNING)

    def _toast(self, text: str, kind: ToastKind) -> None:
        Toast.show_message(self.window(), text, kind)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("报告中心"))
        self.experiment_label.setText(self.tr("实验"))
        self.refresh_button.setText(self.tr("刷新"))
        self.back_to_list.setText(self.tr("返回报告列表"))
        self.list_refresh.setText(self.tr("刷新列表"))
        self.list_export.setText(self.tr("批量导出…"))
        self.import_pdf_button.setText(self.tr("导入外部 PDF…"))
        self.import_open.setText(self.tr("打开匹配的报告"))
        self.report_table.set_columns(
            [
                Column("experiment_id", self.tr("实验"), mono=True),
                Column("created_at", self.tr("时间"), mono=True),
                Column("sample_batches", self.tr("样品批号"), mono=True),
                Column("reagent_batches", self.tr("试剂批号"), mono=True),
                Column("report_state", self.tr("报告状态")),
            ]
        )
        for box, label in (
            (self.sample_filter, self.tr("全部样品批号")),
            (self.lal_filter, self.tr("全部鲎试剂批号")),
            (self.buffer_filter, self.tr("全部缓冲液批号")),
            (self.endotoxin_filter, self.tr("全部内毒素试剂批号")),
        ):
            if box.count():
                box.setItemText(0, label)
        self.empty_title.setText(self.tr("还没有可出报告的实验"))
        self.empty_hint.setText(
            self.tr(
                "实验完成并复核后，在这里预览、打印和签署报告。可以先到历史记录里查看已有的实验。"
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
        self.sign_button.setText(self.tr("签署并生成 PDF"))
        self.print_button.setText(self.tr("打印"))
        self.export_button.setText(self.tr("导出已签名 PDF"))
        self.excel_button.setText(self.tr("导出 Excel 数据…"))
        self.experiment_data_button.setText(self.tr("查看实验数据"))
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
