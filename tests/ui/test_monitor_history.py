"""回放、历史只读回看、重启后回看、工作台的真实数据。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ReagentCheckRecord
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.dashboard import DashboardPage
from gelinspect.ui.pages.history_page import HistoryPage
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.replay_dialog import ReplayDialog
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from gelinspect.ui.widgets.marks import WellState
from tests.ui.conftest import FIXED_NOW, settle, sign_in, wait_for
from tests.ui.monitor_helpers import (
    close_context,
    close_window,
    open_context,
    open_window,
    start_sensitivity,
    wait_finished,
)


def finished_experiment(qapp: QApplication, window: MainWindow, context: AppContext) -> str:
    monitor = start_sensitivity(qapp, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    wait_finished(qapp, context)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    return experiment_id


def test_replay_plays_a_real_mp4_row_by_row(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    experiment_id = finished_experiment(qapp, fast_window, fast_context)
    page = fast_window.page("monitor")
    assert isinstance(page, MonitorPage)
    assert fast_window.logout()
    sign_in(qapp, fast_window, "analyst")
    assert fast_window.open_experiment(experiment_id)
    page = fast_window.page("monitor")
    assert isinstance(page, MonitorPage)

    # 只读页的回放卡：下拉里是有录像的排，自动载入第一排（不自动播放）
    assert page.replay_card.isVisibleTo(page) and not page.video_card.isVisibleTo(page)
    assert [page.replay_rows.itemText(i) for i in range(page.replay_rows.count())] == [
        "Row01",
        "Row02",
        "Row03",
    ]
    player = page.replay_player
    assert player.clip is not None and not player.is_playing
    assert player.view.frames_shown >= 1 and player.slider.maximum() == player.clip.frame_count - 1
    player.play()
    assert wait_for(qapp, lambda: player.view.frames_shown >= 4, 20.0)
    assert player.slider.value() > 0 and "/" in player.time_label.text()
    player.pause()
    assert not player.is_playing
    player.slider.setValue(player.slider.maximum())  # 拖到末尾
    assert player.position == player.slider.maximum()
    player.play()  # 播完后再点播放：从头重播
    assert wait_for(
        qapp, lambda: player.is_playing and player.slider.value() < player.slider.maximum()
    )
    page.replay_rows.setCurrentIndex(2)  # 换排
    assert player.clip is not None and "row_03" in player.clip.path.name
    player.pause()

    # 对话框版（实时页的“回放第 N 排”）：同样能播
    page.open_replay()
    dialog = page.replay_dialog()
    assert isinstance(dialog, ReplayDialog) and dialog.current_row() == 3
    assert wait_for(qapp, lambda: dialog.player.view.frames_shown >= 3, 20.0)
    dialog.rows.setCurrentIndex(0)
    assert dialog.current_row() == 1 and dialog.player.clip is not None
    dialog.close()
    assert dialog.player.clip is None  # 关闭即释放解码器


def test_replay_of_a_changed_recording_shows_why_instead_of_playing(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    experiment_id = finished_experiment(qapp, fast_window, fast_context)
    recordings = fast_context.media.recordings(fast_window.session, experiment_id)
    path = recordings[1].path
    path.write_bytes(path.read_bytes() + b"\0")
    page = fast_window.page("monitor")
    assert isinstance(page, MonitorPage)
    page.open_replay()
    dialog = page.replay_dialog()
    assert dialog is not None
    dialog.rows.setCurrentIndex(dialog.rows.findData(1))
    assert dialog.player.clip is None
    assert "哈希不符" in dialog.player.view._text  # noqa: SLF001
    dialog.close()


def test_history_opens_a_finished_experiment_read_only(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    experiment_id = finished_experiment(qapp, fast_window, fast_context)
    assert fast_window.logout()
    sign_in(qapp, fast_window, "analyst")
    # “打开文件”快捷操作进入历史记录
    fast_window.shell.toolbar.actionTriggered.emit("open_file")
    assert fast_window.current_route == "history"
    history = fast_window.page("history")
    assert isinstance(history, HistoryPage)
    assert history.table.row_count() == 1
    history.table.rowActivated.emit(0)
    assert fast_window.current_route == "monitor"
    page = fast_window.page("monitor")
    assert isinstance(page, MonitorPage)
    assert page.experiment_id == experiment_id and page.read_only
    # 只读：没有停止按钮、实时视频和温度，有结果、回放和图像
    assert not page.stop_button.isVisibleTo(page) and not page.temperature.isVisibleTo(page)
    assert not page.video_card.isVisibleTo(page) and page.replay_card.isVisibleTo(page)
    assert page.table.row_count() == 17 and page.badge.text().startswith("待复核")
    stored = fast_context.database.experiments.get(experiment_id)
    assert stored is not None
    pending = list(stored.pending_review_tubes)
    assert all(page.plate.state(n) is WellState.REVIEW for n in pending)
    assert (
        page.plate.countdown_text(1) == "已读数" if hasattr(page.plate, "countdown_text") else True
    )
    page.tabs.set_current(1)
    assert wait_for(qapp, lambda: len(page.images_tab.loaded_rows()) == 3, 30.0)
    # 点孔进详情，详情能看到掩码，并以只读状态显示复核入口
    page.tabs.set_current(0)
    assert fast_window.open_tube(experiment_id, pending[0])
    detail = fast_window.page("tube")
    assert isinstance(detail, TubeDetailPage)
    assert wait_for(qapp, lambda: detail.images is not None, 30.0)
    assert detail.before_view.has_overlay_data() and detail.submit_button.isEnabled()
    # 返回孔板回到同一个试验
    detail.back_button.click()
    assert fast_window.current_route == "monitor" and page.experiment_id == experiment_id


def test_a_finished_experiment_can_be_reviewed_again_after_a_restart(
    qapp: QApplication, tmp_path: Path, language: LanguageManager
) -> None:
    from tests.services.test_new_experiment_engine import request_for_tubes
    from tests.ui.conftest import DEMO_PASSWORDS

    first = open_context(tmp_path)
    session = first.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    experiment_id = first.experiments.create_experiment(
        session, request_for_tubes(list(range(1, 18)))
    )
    first.experiments.start_experiment(session, experiment_id)
    assert wait_for(
        qapp,
        lambda: (
            (s := first.engine.snapshot()) is not None and s.state is ExperimentState.PENDING_REVIEW
        ),
        90.0,
    )
    assert first.engine.wait(30.0)
    before = first.database.experiments.get(experiment_id)
    assert before is not None
    close_context(first)

    second = open_context(tmp_path)
    window = open_window(qapp, second, language)
    try:
        assert second.recovered == []  # 已完成的试验不会被当成“上次没有正常结束”
        sign_in(qapp, window, "analyst")
        assert window.open_experiment(experiment_id)
        page = window.page("monitor")
        assert isinstance(page, MonitorPage)
        assert page.read_only and page.table.row_count() == 17
        after = second.database.experiments.get(experiment_id)
        assert after is not None and after.state is ExperimentState.PENDING_REVIEW
        assert {n: r.auto_interpretation for n, r in after.results.items()} == {
            n: r.auto_interpretation for n, r in before.results.items()
        }
        view = page.view_model
        assert view is not None and view.started_at is not None
        assert wait_for(qapp, lambda: page.replay_player.clip is not None)  # 录像还能播放
        pending = view.pending_review
        assert window.open_tube(experiment_id, pending[0])
        detail = window.page("tube")
        assert isinstance(detail, TubeDetailPage)
        assert wait_for(qapp, lambda: detail.images is not None, 30.0)
        assert detail.images is not None and detail.images.after is not None
        assert detail.images.after.gel  # 重启后掩码仍能叠加
    finally:
        close_window(qapp, window)
        close_context(second)


def test_dashboard_shows_the_running_experiment_results_and_things_to_handle(
    qapp: QApplication, live_window: MainWindow, live_context: AppContext
) -> None:
    window, context = live_window, live_context
    monitor = start_sensitivity(qapp, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    # 试剂临期：校验记录里有一个 12 天后到期的批次
    expiry = date(2026, 11, 5) + timedelta(days=12)
    context.database.reagent_checks.add(
        ReagentCheckRecord(
            checked_at=FIXED_NOW,
            actor="experimenter",
            kind=ReagentKind.LAL_REAGENT,
            batch_no="LAL-NEAR",
            product_code="P1",
            status=ReagentCheckStatus.VALID,
            registered=True,
            expiry_date=expiry,
        )
    )
    window.navigate("dashboard")
    dashboard = window.page("dashboard")
    assert isinstance(dashboard, DashboardPage)
    assert wait_for(qapp, lambda: dashboard.running_state.isVisibleTo(dashboard))
    assert dashboard.running_id.text() == experiment_id
    assert dashboard.mini_plate.state(1) is WellState.INCUBATING
    assert dashboard.mini_plate.state(17) is WellState.INCUBATING
    assert dashboard.mini_plate.state(18) is WellState.EMPTY
    assert dashboard.mini_plate.state(40) is WellState.EMPTY
    assert dashboard.progress.value == 0.0 and "17 管" in dashboard.running_rows.text()
    assert dashboard.recent_table.row_count() == 1
    texts = [
        dashboard.attention_box.itemAt(i).widget().text()  # type: ignore[union-attr]
        for i in range(dashboard.attention_box.count())
    ]
    assert any("LAL-NEAR" in t and "还有 12 天过期" in t for t in texts)
    # 孔板事件很密，工作台卡片自己合并刷新；点“打开监控”回到监控页
    dashboard.running_open.click()
    assert window.current_route == "monitor"


def test_dashboard_lists_pending_reviews_and_opens_them(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    experiment_id = finished_experiment(qapp, fast_window, fast_context)
    assert fast_window.logout()
    sign_in(qapp, fast_window, "analyst")
    dashboard = fast_window.page("dashboard")
    assert isinstance(dashboard, DashboardPage)
    dashboard.refresh()
    assert not dashboard.running_state.isVisibleTo(dashboard)
    assert dashboard.recent_table.row_count() == 1
    item = dashboard.attention_box.itemAt(0)
    assert item is not None
    link = item.widget()
    assert isinstance(link, QPushButton)
    assert "待复核" in link.text() and experiment_id in link.text()
    link.click()
    assert fast_window.current_route == "monitor"
    page = fast_window.page("monitor")
    assert isinstance(page, MonitorPage) and page.experiment_id == experiment_id
    settle(qapp)
