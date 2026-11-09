"""监控与复核端到端：登录 → 向导 → 启动 → 监控页随引擎更新 → 点孔进详情 → 掩码叠加 → 复核。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.entities import Experiment
from gelinspect.domain.enums import AuditAction, Interpretation
from gelinspect.domain.judgment import SCORE_FEATURES, read_judgment
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditQuery, MediaKind, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.mask_archive import MASK_PREFIX
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from gelinspect.ui.widgets.marks import WellState
from tests.ui.conftest import settle, sign_in, wait_for
from tests.ui.monitor_helpers import (
    click_well,
    start_sensitivity,
    wait_finished,
)


def tube_page(window: MainWindow) -> TubeDetailPage:
    page = window.page("tube")
    assert isinstance(page, TubeDetailPage)
    return page


def test_full_review_flow_from_wizard_to_signable(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    window, context = fast_window, fast_context
    monitor = start_sensitivity(qapp, window)

    # 孵育一开始，监控页就显示 17 管和各排倒计时；孔板随引擎更新
    assert wait_for(qapp, lambda: monitor.view_model is not None and monitor.view_model.live)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    assert monitor.table.row_count() == 17
    assert wait_for(
        qapp,
        lambda: any(
            monitor.plate.state(t) in (WellState.POSITIVE, WellState.NEGATIVE, WellState.REVIEW)
            for t in range(1, 18)
        ),
        60.0,
    )
    wait_finished(qapp, context)
    assert wait_for(qapp, lambda: not monitor.read_only or monitor.badge.text() != "", 10.0)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.PENDING_REVIEW
    pending = list(stored.pending_review_tubes)
    assert pending, "默认场景下应有部分凝固的试管等待复核"
    expected = {
        n: (
            WellState.REVIEW
            if r.needs_review
            else WellState.POSITIVE
            if r.final_interpretation is Interpretation.POSITIVE
            else WellState.NEGATIVE
        )
        for n, r in stored.results.items()
    }
    assert wait_for(qapp, lambda: all(monitor.plate.state(n) == s for n, s in expected.items()))
    assert monitor.badge.text().startswith("待复核")

    # 点孔进详情：原图、掩码文件存在，掩码能叠加
    tube = pending[0]
    click_well(monitor.plate, tube)
    assert window.current_route == "tube"
    detail = window.page("tube")
    assert isinstance(detail, TubeDetailPage) and detail.tube_no == tube
    assert wait_for(qapp, lambda: tube_page(window).images is not None, 30.0)
    images = detail.images
    assert images is not None and images.before is not None and images.after is not None
    media = context.database.media.list_for_experiment(experiment_id, kind=MediaKind.IMAGE)
    masks = [
        m for m in media if Path(m.path).name.startswith(MASK_PREFIX) and f"t{tube:02d}" in m.path
    ]
    assert len(masks) == 4 and all(Path(m.path).is_file() for m in masks)
    assert images.after.gel and images.after.tube
    assert detail.before_view.has_overlay_data()
    with_mask = detail.after_view.grab().toImage()
    detail.mask_toggle.setChecked(False)
    settle(qapp)
    without_mask = detail.after_view.grab().toImage()
    assert with_mask != without_mask
    detail.mask_toggle.setChecked(True)

    # 实验员不能复核：界面禁用，服务层也拒绝
    assert not detail.submit_button.isEnabled()
    session = window.session
    try:
        context.reviews.review_tube(session, experiment_id, tube, Interpretation.POSITIVE, "x")
    except PermissionDeniedError:
        pass
    else:
        raise AssertionError("实验员不应能复核")
    assert (
        context.database.experiments.get(experiment_id).results[tube].reviewed_by is None  # type: ignore[union-attr]
    )

    # 分析员复核：意见必填；逐管处理后试验转可签署
    assert window.logout()
    sign_in(qapp, window, "analyst")
    for number, tube_no in enumerate(pending):
        assert window.open_tube(experiment_id, tube_no)
        detail = tube_page(window)
        assert wait_for(qapp, lambda: tube_page(window).images is not None, 30.0)
        assert detail.submit_button.isEnabled()
        if number == 0:
            check_judgment(qapp, detail, context.database.experiments.get(experiment_id), tube_no)  # type: ignore[arg-type]
        detail.positive_button.click()
        detail.submit_button.click()  # 没写意见
        assert detail.review_error.isVisibleTo(detail) and "必填" in detail.review_error.text()
        assert (
            context.database.experiments.get(experiment_id).results[tube_no].reviewed_by is None  # type: ignore[union-attr]
        )
        detail.note.setPlainText(f"复核意见 {number}")
        detail.submit_button.click()
        settle(qapp)
        row = context.database.experiments.get(experiment_id)
        assert row is not None
        result = row.results[tube_no]
        assert result.reviewed_interpretation is Interpretation.POSITIVE
        assert result.reviewed_by == "analyst" and result.review_note == f"复核意见 {number}"
        assert result.auto_interpretation is Interpretation.PENDING_REVIEW  # 算法结果不被覆盖
        assert (
            "算法：" in detail.verdict_title.text() and "人工：阳性" in detail.verdict_title.text()
        )
    final = context.database.experiments.get(experiment_id)
    assert final is not None and final.state is ExperimentState.COMPLETED
    page = context.database.audit.query(
        AuditQuery(action=AuditAction.TUBE_REVIEWED), PageRequest(limit=100)
    )
    assert len(page.items) == len(pending)
    window.navigate("monitor")
    reopened = window.page("monitor")
    assert isinstance(reopened, MonitorPage)
    assert wait_for(qapp, lambda: "已完成" in reopened.badge.text())


def check_judgment(
    qapp: QApplication, detail: TubeDetailPage, experiment: Experiment, tube_no: int
) -> None:
    """判读过程卡：四个评分分项、加权和、阈值、三态到阴阳性；判读依据对话框列出映射。"""
    result = experiment.results[tube_no]
    basis = read_judgment(result.features)
    assert [t.feature for t in basis.terms] == list(SCORE_FEATURES)
    assert basis.total == pytest.approx(result.score, abs=1e-6)
    assert basis.thresholds is not None
    assert basis.thresholds.state_for(result.score or 0) is result.gel_state
    assert detail.score_bar.thresholds == basis.thresholds
    assert detail.judgment_cells[4][4].text() == f"{basis.total:.3f}"
    assert "→ 待复核" in detail.mapping_label.text()
    guide = detail.open_guide()
    settle(qapp)
    assert guide.isVisible()
    assert [r[1] for r in guide.mapping_rows] == ["阳性", "待复核", "阴性"]
    guide.close()
    settle(qapp)
