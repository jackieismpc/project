"""报告中心：真实向导启动模拟试验 → 复核 → 签署 → 验签 → 篡改 → 导出，界面全程真实控件。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, Interpretation, PermissionGroup
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.reporting.report_service import ReportListItem
from gelinspect.services.reporting.texts import ReportLanguage
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import TAB_REPORT, MonitorPage
from gelinspect.ui.pages.report_dialogs import BatchExportDialog, SignDialog
from gelinspect.ui.pages.reports_page import ReportsPage
from tests.services.reporting.pdftext import has_text
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in, wait_for
from tests.ui.monitor_helpers import start_sensitivity, wait_finished


def reports_of(window: MainWindow) -> ReportsPage:
    page = window.page("reports")
    assert isinstance(page, ReportsPage)
    return page


def finish_experiment(app: QApplication, window: MainWindow, context: AppContext) -> str:
    """走真实向导跑完模拟灵敏度试验，并由实验与结果处理用户复核到“已完成”。"""
    monitor = start_sensitivity(app, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    wait_finished(app, context)
    operator = context.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None
    for tube in stored.pending_review_tubes:
        context.reviews.review_tube(
            operator, experiment_id, tube, Interpretation.POSITIVE, "人工判阳性"
        )
    window.logout(confirm=False)
    settle(app)
    return experiment_id


def click(app: QApplication, widget: object) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)  # type: ignore[call-overload]
    settle(app)


def auto_sign(monkeypatch: pytest.MonkeyPatch, password: str) -> list[str]:
    """把签署对话框的 exec 换成：填密码、点“签署”，返回对话框里显示过的错误。"""
    errors: list[str] = []

    def fake_exec(self: SignDialog) -> int:
        self.password.setText(password)
        click_ok = self.ok_button
        click_ok.click()
        if self.error_label.text():
            errors.append(self.error_label.text())
        return int(self.result())

    monkeypatch.setattr(SignDialog, "exec", fake_exec)
    return errors


def test_end_to_end_sign_verify_tamper_export(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window, context = fast_window, fast_context
    admin = context.auth.login("admin", DEMO_PASSWORDS["admin"])
    experimenter = context.database.users.get_by_username("experimenter")
    assert experimenter is not None
    context.users.set_permission_groups(
        admin,
        experimenter.user_id,
        frozenset({PermissionGroup.EXPERIMENT, PermissionGroup.OUTPUT}),
    )
    experiment_id = finish_experiment(qapp, window, context)

    # 实验与结果处理用户可以签署。
    sign_in(qapp, window, "experimenter")
    assert window.navigate("reports")
    page = reports_of(window)
    assert page.open_experiment(experiment_id)
    assert page.experiment_id == experiment_id
    assert page.sign_button.isEnabled()
    assert page.print_button.isEnabled()
    window.logout(confirm=False)
    settle(qapp)

    # 同一用户再次确认密码；密码错误时对话框保持打开并说明。
    sign_in(qapp, window, "experimenter")
    assert window.navigate("reports")
    page = reports_of(window)
    assert page.open_experiment(experiment_id)
    assert page.preview_widget.pageCount() >= 1
    assert page.current_preview is not None and page.current_preview.can_sign
    assert page.sign_button.isEnabled()
    errors = auto_sign(monkeypatch, "wrong-password")
    click(qapp, page.sign_button)
    assert errors and "密码" in errors[0]
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.COMPLETED

    # 正确密码：签署成功，界面显示已签署，状态被锁定
    auto_sign(monkeypatch, DEMO_PASSWORDS["experimenter"])
    signed: list[str] = []
    page.signedExperiment.connect(signed.append)
    click(qapp, page.sign_button)
    assert signed == [experiment_id]
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.SIGNED
    record = page.current_record
    assert record is not None and record.signed
    assert page.status_text.text() == "已签署"
    assert not page.sign_button.isEnabled()
    assert not hasattr(page, "language_box")
    path = context.reports.resolve(record)

    # 列表首页可直接导入任意外部 PDF；有效签名与数据库报告匹配后可打开对应实验。
    page.show_report_list()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )
    click(qapp, page.import_pdf_button)
    assert page.import_open.isVisible()
    assert "签名有效" in page.import_result.text()
    click(qapp, page.import_open)
    assert page.experiment_id == experiment_id

    # 重新验签通过
    click(qapp, page.verify_button)
    result = page.last_verification
    assert result is not None and result.ok
    assert "验签通过" in page.verify_result.text()

    # 篡改一个字节：界面显示明确原因
    original = path.read_bytes()
    broken = bytearray(original)
    broken[len(broken) // 2] ^= 0x01
    path.write_bytes(bytes(broken))
    click(qapp, page.verify_button)
    failed = page.last_verification
    assert failed is not None and not failed.ok
    text = page.verify_result.text()
    assert "验签未通过" in text and "SHA-256" in text
    path.write_bytes(original)

    # 导出同一份文件 + 批量导出（管理员和分析员有 data_export）
    target = tmp_path / "exported.pdf"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    click(qapp, page.export_button)
    assert target.read_bytes() == original
    assert has_text(original, f"{experiment_id}")

    def batch_exec(self: BatchExportDialog) -> int:
        self.directory.setText(str(tmp_path / "bulk"))
        self.export_button.click()
        assert self.export_result is not None and self.export_result.exported == 1
        assert "已导出 1 份" in self.result_label.text()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(BatchExportDialog, "exec", batch_exec)
    page.show_report_list()
    page.report_table.set_rows(page.report_table.model.rows)
    page.report_table.view.selectRow(0)
    assert page.list_export.isEnabled()
    click(qapp, page.list_export)
    assert (tmp_path / "bulk" / f"{experiment_id}.pdf").read_bytes() == original
    assert (tmp_path / "bulk" / "manifest.csv").is_file()

    # 打印：写到 PDF 打印机，并留审计
    out = tmp_path / "printed.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(out))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    assert page.print_to(printer) >= 1
    assert out.stat().st_size > 1000
    printed = context.database.audit.query(
        AuditQuery(action=AuditAction.REPORT_PRINTED), PageRequest()
    )
    assert any("打印报告" in r.event.detail for r in printed.items)


def test_monitor_report_tab_and_quick_action_open_report(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    window, context = fast_window, fast_context
    experiment_id = finish_experiment(qapp, window, context)
    sign_in(qapp, window, "analyst")
    # 监控页报告预览与快捷栏都定位到独立报告。
    assert window.open_experiment(experiment_id)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    monitor.tabs.set_current(TAB_REPORT)
    settle(qapp)
    assert monitor.report_open.isEnabled()
    click(qapp, monitor.report_open)
    assert window.current_route == "reports" and reports_of(window).experiment_id == experiment_id
    # 快捷栏入口打开报告中心首页。
    assert window.open_experiment(experiment_id)
    window._on_quick_action("reports")
    assert window.current_route == "reports" and reports_of(window).list_card.isVisible()


def test_empty_state_and_language_follow(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
) -> None:
    sign_in(qapp, window, "analyst")
    assert window.navigate("reports")
    page = reports_of(window)
    assert page.list_card.isVisible() and not page.content.isVisible()
    assert page.report_table.row_count() == 0
    page._report_items = [
        ReportListItem(
            experiment_id=f"EXP-{number:04d}",
            experiment_type="sensitivity",
            created_at=datetime(2026, 11, 17, tzinfo=UTC),
            state=ExperimentState.COMPLETED,
            sample_batch_nos=(f"SAMPLE-{number:04d}",),
            lal_batch_no=f"LAL-{number:04d}",
            endotoxin_batch_no=f"ENDO-{number:04d}",
            water_batch_no=f"WATER-{number:04d}",
            latest_report=None,
        )
        for number in range(201)
    ]
    page._refresh_report_list()
    assert page.report_table.row_count() == 201
    assert any(row["experiment_id"] == "EXP-0200" for row in page.report_table.model.rows)
    for box, value in (
        (page.sample_filter, "SAMPLE-0200"),
        (page.lal_filter, "LAL-0200"),
        (page.buffer_filter, "WATER-0200"),
        (page.endotoxin_filter, "ENDO-0200"),
    ):
        index = box.findData(value)
        assert index >= 0
        box.setCurrentIndex(index)
    assert page.report_table.row_count() == 1
    assert page.report_table.model.rows[0]["experiment_id"] == "EXP-0200"
    language.set_language(Language.EN)
    settle(qapp)
    assert page.import_pdf_button.text() == "Verify an external PDF…"
    assert page._ui_language is ReportLanguage.EN
    language.set_language(Language.ZH_CN)
    assert ReportLanguage.EN.value == "en"


def test_report_center_fits_in_both_languages_and_sizes(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    language: LanguageManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.ui.test_layout_languages import SIZES, overflow_report, use

    window, context = fast_window, fast_context
    experiment_id = finish_experiment(qapp, window, context)
    sign_in(qapp, window, "analyst")
    for lang in (Language.ZH_CN, Language.ZH_TW, Language.EN):
        for size in SIZES:
            use(window, language, lang, size)
            assert window.open_report(experiment_id)
            page = reports_of(window)
            page.verify_result.setText("验签未通过：\n· 文件内容与签名不符：签署之后被修改过。")
            page.verify_result.setVisible(True)
            settle(qapp)
            report = overflow_report(window)
            assert report == [], f"报告中心（未签署）/ {lang.value} / {size}：{report}"
    # 已签署
    use(window, language, Language.ZH_CN, SIZES[0])
    auto_sign(monkeypatch, DEMO_PASSWORDS["analyst"])
    assert window.open_report(experiment_id)
    click(qapp, reports_of(window).sign_button)
    for lang in (Language.ZH_CN, Language.ZH_TW, Language.EN):
        for size in SIZES:
            use(window, language, lang, size)
            assert window.open_report(experiment_id)
            click(qapp, reports_of(window).verify_button)
            report = overflow_report(window)
            assert report == [], f"报告中心（已签署）/ {lang.value} / {size}：{report}"
    assert wait_for(qapp, lambda: True)


def test_preview_layout_settles_instead_of_oscillating(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    """“适应宽度”的预览曾因滚动条时有时无而无限重排（界面卡死）：布局必须稳定下来。"""
    from PySide6.QtCore import QEvent, QObject

    window, context = fast_window, fast_context
    experiment_id = finish_experiment(qapp, window, context)
    sign_in(qapp, window, "analyst")
    window.resize(1280, 900)
    assert window.open_report(experiment_id)
    resizes = 0

    class Counter(QObject):
        def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
            nonlocal resizes
            resizes += event.type() == QEvent.Type.Resize
            return False

    counter = Counter()
    qapp.installEventFilter(counter)
    page = reports_of(window)
    page.verify_result.setText(
        "验签未通过：\n· 文件的 SHA-256 与库里记录的不一致\n· 文件内容与签名不符"
    )
    page.verify_result.setVisible(True)
    settle(qapp, 30)
    qapp.removeEventFilter(counter)
    assert resizes < 200, resizes


def test_external_verification_selects_match_and_resets_on_navigation(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    sign_in(qapp, window, "analyst")
    assert window.navigate("reports")
    page = reports_of(window)
    page._report_items = [
        ReportListItem(
            experiment_id=f"EXP-{number}",
            experiment_type="sensitivity",
            created_at=datetime(2026, 11, 17, tzinfo=UTC),
            state=ExperimentState.COMPLETED,
            sample_batch_nos=(f"SAMPLE-{number}",),
            lal_batch_no="LAL",
            endotoxin_batch_no="ENDO",
            water_batch_no="WATER",
            latest_report=None,
        )
        for number in range(2)
    ]
    page._refresh_report_list()
    page.sample_filter.setCurrentIndex(page.sample_filter.findData("SAMPLE-0"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("sample.pdf", ""))
    result = SimpleNamespace(signature_status="valid", experiment_id="EXP-1", message="matched")
    monkeypatch.setattr(page._services.reports, "verify_imported_file", lambda *a: result)
    page.import_external_pdf()
    assert page.import_result.property("role") == "ok"
    assert (
        page.report_table.model.rows[page.report_table.selected_rows()[0]]["experiment_id"]
        == "EXP-1"
    )
    result.signature_status = "invalid"
    page.import_external_pdf()
    assert page.import_result.property("role") == "danger"
    assert not page.report_table.selected_rows()
    assert window.navigate("dashboard")
    assert page.import_result.text() == "" and page._import_verification is None
