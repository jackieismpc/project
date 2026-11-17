"""监控页的真实报告预览：按需排版，支持全部页面和逐页查看。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewWidget
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.reporting.renderer import configure_printer
from gelinspect.services.reporting.report_service import ReportPreview, ReportService
from gelinspect.services.reporting.texts import ReportLanguage, report_language_of
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
)
from gelinspect.ui.widgets.button import Button
from gelinspect.ui.widgets.label import Label

_log = logging.getLogger(__name__)


class MonitorReportPreview(QWidget):
    """使用报告中心同一排版服务显示当前实验，隐藏时不执行排版。"""

    def __init__(self, reports: ReportService, parent: QWidget | None = None) -> None:
        """创建预览，尚未绑定实验。"""
        super().__init__(parent)
        self._reports = reports
        self._session: Session | None = None
        self._experiment_id: str | None = None
        self._language = ReportLanguage.ZH_CN
        self._revision: object = None
        self._active = False
        self._rendering = False
        self._paint_failed = False
        self.current_preview: ReportPreview | None = None
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_printer(self._printer)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.overview_button = Button("")
        self.pages = QComboBox()
        self.pages.setMinimumHeight(44)
        self.refresh_button = Button("")
        toolbar.addWidget(self.overview_button)
        toolbar.addWidget(self.pages)
        toolbar.addStretch(1)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)
        self.message = Label("", "meta")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.preview_widget = QPrintPreviewWidget(self._printer, self)
        self.preview_widget.setMinimumHeight(540)
        self.preview_widget.setAccessibleName("monitor-report-preview")
        self.preview_widget.paintRequested.connect(self._paint)
        layout.addWidget(self.preview_widget)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.overview_button.clicked.connect(self._overview)
        self.pages.activated.connect(self._select_page)
        self.retranslate()
        self.clear()

    def clear(self) -> None:
        """清除旧报告显示，取消尚未执行的刷新。"""
        self._timer.stop()
        self.current_preview = None
        self.preview_widget.hide()
        self.pages.clear()
        self.pages.setEnabled(False)
        self.overview_button.setEnabled(False)
        self.message.clear()

    def set_context(
        self,
        session: Session | None,
        experiment_id: str | None,
        revision: object,
    ) -> None:
        """绑定实验及数据版本，变化时立即隐藏过期画面。"""
        if (
            session is not self._session
            or experiment_id != self._experiment_id
            or revision != self._revision
        ):
            self.clear()
            self._session, self._experiment_id, self._revision = session, experiment_id, revision
            if self._active:
                self._timer.start()
        self.refresh_button.setEnabled(session is not None and experiment_id is not None)

    def set_ui_language(self, code: str) -> None:
        """更换预览语言，已签署报告仍由服务选择原签署语言。"""
        language = report_language_of(code)
        if language != self._language:
            self._language = language
            self.clear()
            if self._active:
                self._timer.start()

    def set_active(self, active: bool) -> None:
        """进入报告页签时刷新，离开时取消尚未执行的任务。"""
        self._active = active
        if active:
            self.refresh()
        elif not active:
            self._timer.stop()

    def refresh(self) -> None:
        """重新授权、读取和排版；失败时不展示上一个实验的报告。"""
        self._timer.stop()
        if self._rendering or not self._active:
            return
        self.clear()
        if self._session is None or self._experiment_id is None:
            self.message.setText(self.tr("先选择一个试验。"))
            return
        self._rendering = True
        self.refresh_button.setEnabled(False)
        try:
            self.current_preview = self._reports.preview(
                self._session, self._experiment_id, self._language
            )
            self._paint_failed = False
            self.preview_widget.updatePreview()
            if self._paint_failed:
                return
            total = self.preview_widget.pageCount()
            if total < 1:
                raise RuntimeError("report preview returned no pages")
            for page in range(1, total + 1):
                self.pages.addItem(self.tr("第 {page} 页").format(page=page), page)
            self.pages.setEnabled(True)
            self.overview_button.setEnabled(True)
            self._overview()
            self.preview_widget.show()
        except (AuthError, ServiceError, StorageBusyError) as error:
            _log.warning(
                "REPORT_PREVIEW_LOAD_FAILED experiment=%s code=%s: %s",
                self._experiment_id,
                getattr(error, "code", type(error).__name__),
                error,
                exc_info=True,
            )
            if isinstance(error, AuthError):
                text = describe_auth_error(error)
            elif isinstance(error, ServiceError):
                text = describe_service_error(error)
            else:
                text = describe_storage_busy()
            self.current_preview = None
            self.message.setText(text)
        except Exception:
            _log.exception("REPORT_PREVIEW_LOAD_FAILED experiment=%s", self._experiment_id)
            self._show_failure()
        finally:
            self._rendering = False
            self.refresh_button.setEnabled(True)

    def _paint(self, printer: QPrinter) -> None:
        # 异常不能穿过 Qt 回调边界；失败状态在 updatePreview 返回后由界面处理。
        if self.current_preview is None:
            return
        try:
            self._reports.paint(self._session, self.current_preview, printer)
        except Exception:
            self._paint_failed = True
            _log.exception("REPORT_PREVIEW_PAINT_FAILED experiment=%s", self._experiment_id)
            self._show_failure()

    def _show_failure(self) -> None:
        self.current_preview = None
        self.preview_widget.hide()
        self.message.setText(self.tr("报告预览暂时无法显示，请点击“刷新预览”重试。"))

    def _overview(self) -> None:
        self.preview_widget.setViewMode(QPrintPreviewWidget.ViewMode.AllPagesView)
        self.preview_widget.setZoomMode(QPrintPreviewWidget.ZoomMode.FitInView)

    def _select_page(self, index: int) -> None:
        page = self.pages.itemData(index)
        if page is not None and self.current_preview is not None:
            self.preview_widget.setViewMode(QPrintPreviewWidget.ViewMode.SinglePageView)
            self.preview_widget.setCurrentPage(int(page))
            self.preview_widget.setZoomMode(QPrintPreviewWidget.ZoomMode.FitInView)

    def retranslate(self) -> None:
        """刷新预览操作文字。"""
        self.overview_button.setText(self.tr("全部页面"))
        self.refresh_button.setText(self.tr("刷新预览"))
        self.pages.setAccessibleName(self.tr("选择报告页码"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """处理界面语言切换。"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
