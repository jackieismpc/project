"""恢复和重试、向导 λ、单管图比例、开发用语。"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import (
    AuditAction,
    ReagentKind,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.analysis import AnalyzerError
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.new_experiment import NewExperimentPage
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in, wait_for
from tests.ui.monitor_helpers import (
    close_context,
    close_window,
    open_context,
    open_window,
    start_sensitivity,
    wait_finished,
)
from tests.ui.wizard_helpers import (
    STEP_SAMPLES,
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    set_tubes,
)

ROOT = Path(__file__).resolve().parents[2]


def monitor_of(window: MainWindow) -> MonitorPage:
    page = window.page("monitor")
    assert isinstance(page, MonitorPage)
    return page


# ---------------------------------------------------------------- 恢复和重试


def break_analyzer(context: AppContext) -> None:
    """让算法每一排都整体失败（配置文件每次加载都会重置 ``fail_all``，所以换掉方法）。"""

    def fail(_request: object) -> None:
        raise AnalyzerError("模拟的算法整体故障")

    context.analyzer.analyze_row = fail  # type: ignore[method-assign,assignment]


def repair_analyzer(context: AppContext) -> None:
    del context.analyzer.analyze_row


def fail_row_analysis(qapp: QApplication, window: MainWindow, context: AppContext) -> MonitorPage:
    """用算法整体故障跑完一排：全部读完后试验转异常，等待重试。"""
    break_analyzer(context)
    monitor = start_sensitivity(qapp, window, tubes=range(1, 19))
    assert wait_for(
        qapp,
        lambda: (s := context.engine.snapshot()) is not None and s.state is ExperimentState.FAULTED,
        90.0,
    )
    monitor.reload()
    settle(qapp)
    return monitor


def test_retry_analysis_from_the_monitor_page(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    window, context = fast_window, fast_context
    monitor = fail_row_analysis(qapp, window, context)
    view = monitor.view_model
    assert view is not None and view.can_resume and view.retry_rows
    assert not monitor.resume_button.isHidden() and not monitor.retry_button.isHidden()
    assert monitor.retry_button.isEnabled()
    assert monitor.resume_button.text() == "恢复实验"
    assert monitor.retry_button.text() == "重试分析"

    repair_analyzer(context)
    click(qapp, monitor.retry_button)
    assert wait_for(
        qapp,
        lambda: (
            (s := context.engine.snapshot()) is not None
            and s.state in (ExperimentState.PENDING_REVIEW, ExperimentState.COMPLETED)
        ),
        60.0,
    )
    assert context.engine.wait(30.0)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    records = context.database.audit.query(
        AuditQuery(experiment_id=experiment_id, action=AuditAction.EXPERIMENT_STATE_CHANGED),
        PageRequest(limit=100),
    )
    assert any(
        r.event.actor == "experimenter" and "resume_analysis" in r.event.detail
        for r in records.items
    )
    assert wait_for(qapp, lambda: monitor.retry_button.isHidden(), 10.0)


def test_resume_requires_confirmation_and_permission(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, context = fast_window, fast_context
    monitor = fail_row_analysis(qapp, window, context)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None

    answers: list[bool] = [False]
    monkeypatch.setattr(
        "gelinspect.ui.pages.monitor_page.ConfirmDialog.ask", lambda *_a, **_k: answers[0]
    )
    assert monitor.request_resume() is False  # 取消：什么都不做
    assert context.engine.snapshot().state is ExperimentState.FAULTED  # type: ignore[union-attr]

    # 没有实验与结果处理组的只读用户不能操作，服务层也拒绝并写审计
    from gelinspect.domain.enums import PermissionGroup

    admin = context.auth.login("admin", DEMO_PASSWORDS["admin"])
    user = context.database.users.get_by_username("analyst")
    assert user is not None
    context.users.set_permission_groups(admin, user.user_id, frozenset({PermissionGroup.VIEW}))
    session = context.auth.login("analyst", DEMO_PASSWORDS["analyst"])
    experimenter = monitor._session  # noqa: SLF001
    monitor.set_session(session)
    settle(qapp)
    assert not monitor.resume_button.isEnabled() and not monitor.retry_button.isEnabled()
    with pytest.raises(PermissionDeniedError):
        context.monitor.resume(session, experiment_id)
    with pytest.raises(PermissionDeniedError):
        context.monitor.retry_analysis(session, experiment_id)
    denied = context.database.audit.query(
        AuditQuery(action=AuditAction.PERMISSION_DENIED), PageRequest(limit=20)
    )
    assert any("experiment_run" in r.event.detail for r in denied.items)

    # 实验员确认后恢复；模拟设备一切正常，分析失败的排用原图重试
    repair_analyzer(context)
    monitor.set_session(experimenter)
    settle(qapp)
    assert monitor.resume_button.isEnabled() and monitor.retry_button.isEnabled()
    answers[0] = True
    assert monitor.request_resume() is True
    assert wait_for(
        qapp,
        lambda: (
            (s := context.engine.snapshot()) is not None
            and s.state in (ExperimentState.PENDING_REVIEW, ExperimentState.COMPLETED)
        ),
        60.0,
    )


def test_after_restart_only_retry_and_stop_are_offered(
    qapp: QApplication, tmp_path: Path, language: LanguageManager
) -> None:
    from tests.services.test_new_experiment_engine import request_for_tubes

    first = open_context(tmp_path)
    break_analyzer(first)
    session = first.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    experiment_id = first.experiments.create_experiment(
        session, request_for_tubes(list(range(1, 19)))
    )
    first.experiments.start_experiment(session, experiment_id)
    assert wait_for(
        qapp,
        lambda: (s := first.engine.snapshot()) is not None and s.state is ExperimentState.FAULTED,
        90.0,
    )
    close_context(first)

    second = open_context(tmp_path)
    window = open_window(qapp, second, language)
    try:
        sign_in(qapp, window, "experimenter")
        window.navigate("monitor")
        monitor = monitor_of(window)
        assert monitor.open_experiment(experiment_id)
        settle(qapp)
        view = monitor.view_model
        assert view is not None and view.interrupted and not view.can_resume
        assert view.retry_rows == (1, 2, 3)
        assert monitor.resume_button.isHidden()
        assert not monitor.retry_button.isHidden() and monitor.retry_button.isEnabled()
        assert "完成后实验照常结束" in monitor.recovery_hint.text()
        assert not monitor.recovery_hint.isHidden()
        with_session = second.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
        try:
            second.monitor.resume(with_session, experiment_id)
        except ServiceError as error:
            assert error.code is ServiceErrorCode.RESUME_NOT_ALLOWED
        else:
            raise AssertionError("重启后不应该能恢复")

        click(qapp, monitor.retry_button)
        assert wait_for(
            qapp,
            lambda: (
                (e := second.database.experiments.get(experiment_id)) is not None
                and e.state in (ExperimentState.PENDING_REVIEW, ExperimentState.COMPLETED)
            ),
            60.0,
        )
        assert second.engine.wait(30.0)
        stored = second.database.experiments.get(experiment_id)
        assert stored is not None and set(stored.results) == set(range(1, 19))
        assert wait_for(qapp, lambda: monitor.retry_button.isHidden(), 10.0)
    finally:
        close_window(qapp, window)
        close_context(second)


# ---------------------------------------------------------------- 向导


def sample_step(qapp: QApplication, window: MainWindow) -> NewExperimentPage:
    page = open_wizard(qapp, window, "experimenter")
    choose_template(qapp, page, "灵敏度复核 (2λ:1)")
    click(qapp, page.template_next)
    pass_reagents(qapp, page)
    return page


def test_wizard_shows_spikes_calculated_from_lambda(qapp: QApplication, window: MainWindow) -> None:
    page = sample_step(qapp, window)
    click(qapp, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES
    table = page.sample_table
    assert [table.cell_text(r, 4) for r in (0, 4, 8, 12, 16)] == [
        "0.5",
        "0.25",
        "0.125",
        "0.0625",
        "0",
    ]
    # 由 λ 算出的列不能手工改
    assert not table.cell_item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable
    assert table.cell_item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    set_tubes(qapp, page, range(1, 19))
    click(qapp, page.sample_next)
    summary = page.summary_table
    assert summary.row_count() == 18
    model = summary.view.model()
    assert model.index(0, 3).data() == "0.5"
    assert model.index(12, 3).data() == "0.0625"


def test_wizard_blocks_when_lambda_is_missing(qapp: QApplication, window: MainWindow) -> None:
    page = sample_step(qapp, window)
    scan = page._scans[ReagentKind.LAL_REAGENT]  # noqa: SLF001
    page._scans[ReagentKind.LAL_REAGENT] = replace(scan, labeled_sensitivity=None)  # noqa: SLF001
    page._sync_lambda()  # noqa: SLF001
    page._render()  # noqa: SLF001
    settle(qapp)
    assert page.draft().lambda_missing
    assert not page.reagent_next.isEnabled()
    assert "λ" in page.reagent_next.toolTip()
    assert not page.lock_banner.isHidden()
    page._go(STEP_SAMPLES)  # noqa: SLF001
    assert page.current_step() != STEP_SAMPLES


# ---------------------------------------------------------------- 单管详情和开发用语


def test_tube_image_is_narrowed_to_the_tube_aspect(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    window, context = fast_window, fast_context
    monitor = start_sensitivity(qapp, window)
    wait_finished(qapp, context)
    assert context.engine.wait(30.0)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    window.navigate("tube")
    tube = window.page("tube")
    assert isinstance(tube, TubeDetailPage)
    assert tube.open_tube(experiment_id, 1)
    assert wait_for(qapp, lambda: tube.images is not None and tube.before_view.has_image(), 30.0)
    settle(qapp)
    images = tube.images
    assert images is not None
    height, width = images.before.image.shape[:2]  # type: ignore[union-attr]
    assert height > width * 1.3, "裁剪图应该是竖长的试管"
    view = tube.before_view
    assert abs(view.width() / view.height() - width / height) < 0.08, "控件比例应接近图像"


def test_no_development_phase_in_visible_texts() -> None:
    for name in ("gelinspect_en.ts", "gelinspect_zh_CN.ts"):
        source = (ROOT / "translations" / name).read_text(encoding="utf-8")
        for text in re.findall(r"<source>(.*?)</source>", source, flags=re.S):
            assert not re.search(r"\bP\d+\b", text), text
