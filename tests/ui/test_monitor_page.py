"""监控页：预览帧、慢界面、告警横幅、停止、回放、只读回看、合并刷新。"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.engine_types import (
    AlertCode,
    AlertLevel,
    EngineAlert,
    RowCountdown,
    RowPhase,
    WellStatus,
    WellUpdated,
)
from gelinspect.services.video.events import VideoEvent, VideoEventCode, VideoEventLevel
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.marks import WellState
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource
from tests.ui.conftest import settle, sign_in, wait_for
from tests.ui.monitor_helpers import (
    start_sensitivity,
)


def post_from_thread(call: object, *args: object) -> None:
    """像引擎和预览线程那样，从非主线程调用桥的回调。"""
    thread = threading.Thread(target=call, args=args)  # type: ignore[arg-type]
    thread.start()
    thread.join()


def has(monitor: MonitorPage, key: str) -> bool:
    return key in monitor.alerts.active_keys()


def alert(monitor: MonitorPage, code: AlertCode, level: AlertLevel, text: str) -> EngineAlert:
    assert monitor.experiment_id is not None
    return EngineAlert(monitor.experiment_id, datetime.now(UTC), level, code, text)


# ---------------------------------------------------------------------------- 实时视频


def test_preview_frames_reach_the_view_only_on_the_main_thread(
    qapp: QApplication, live_window: MainWindow, live_context: AppContext
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    main = threading.get_ident()
    source_threads: set[int] = set()
    live_context.monitor.add_preview_listener(lambda _f: source_threads.add(threading.get_ident()))
    received: list[int] = []
    live_window.bridge.previewFrame.connect(lambda _img: received.append(threading.get_ident()))
    assert wait_for(qapp, lambda: monitor.video.frames_shown >= 4, 30.0)
    assert source_threads and main not in source_threads  # 预览在管线自己的线程上产生
    assert received and set(received) == {main}  # 界面只在主线程收到
    image = monitor.video.image()
    assert image is not None and not image.isNull() and image.width() > 0
    # 视频卡信息：当前排、状态、分辨率和帧率、算法耗时、翻转角度
    assert "640×360" in monitor.video_right.text() and "fps" in monitor.video_right.text()
    assert "孵育中" in monitor.video_state.text() or "翻转" in monitor.video_state.text()
    assert "算法判读" in monitor.algo_info.text() and "翻转角度" in monitor.algo_info.text()


def test_a_slow_ui_drops_preview_frames_and_never_blocks_the_engine(
    qapp: QApplication, live_window: MainWindow, live_context: AppContext
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    bridge = live_window.bridge
    assert wait_for(qapp, lambda: monitor.video.frames_shown >= 2, 30.0)
    received, delivered = bridge.preview_received, bridge.preview_delivered
    # 主线程停止处理事件：预览线程照常送帧，但只有最新一帧留着
    deadline = time.monotonic() + 30.0
    while bridge.preview_received < received + 8 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert bridge.preview_received >= received + 8
    qapp.processEvents()
    assert bridge.preview_delivered - delivered <= 2
    # 引擎、视频线程都没有被拖慢：管线仍在出帧
    seen = live_context.pipeline.stats().frames_captured
    deadline = time.monotonic() + 30.0
    while live_context.pipeline.stats().frames_captured < seen + 5 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert live_context.pipeline.stats().frames_captured >= seen + 5


def test_the_engine_finishes_while_the_main_thread_never_pumps_events(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    from tests.services.test_new_experiment_engine import request_for_tubes

    context = fast_context
    from tests.ui.conftest import DEMO_PASSWORDS

    session = context.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    experiment_id = context.experiments.create_experiment(
        session, request_for_tubes(list(range(1, 18)))
    )
    context.experiments.start_experiment(session, experiment_id)

    def finished() -> bool:  # 不调用 processEvents：界面完全没有响应
        snapshot = context.engine.snapshot()
        return snapshot is not None and snapshot.state is ExperimentState.PENDING_REVIEW

    deadline = time.monotonic() + 90.0
    while not finished() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert finished()
    assert context.engine.wait(30.0)
    # 界面恢复后，排队的事件一次性处理完，没有丢失
    settle(qapp, 10)


# ---------------------------------------------------------------------------- 告警横幅


def test_real_temperature_and_video_faults_raise_and_clear_banners(
    qapp: QApplication, live_window: MainWindow, live_context: AppContext
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    device = live_context.device
    assert isinstance(device, SimDevice)
    device.inject_temperature_excursion(+3.0)
    assert wait_for(qapp, lambda: has(monitor, "engine:temperature_excursion"), 30.0)
    assert "37" in monitor.alerts.text_of("engine:temperature_excursion")
    device.clear_faults()
    assert wait_for(qapp, lambda: not has(monitor, "engine:temperature_excursion"), 30.0)

    source = live_context.video_source
    assert isinstance(source, SimVideoSource)
    source.inject_disconnect()
    assert wait_for(qapp, lambda: has(monitor, "engine:video_disconnected"), 30.0)
    assert monitor.alerts.isVisibleTo(monitor)
    assert wait_for(qapp, lambda: not has(monitor, "engine:video_disconnected"), 30.0)


def test_other_alerts_database_busy_and_algorithm_failure_and_video_events(
    qapp: QApplication, live_window: MainWindow
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    bridge = live_window.bridge
    post_from_thread(
        bridge.handle_event,
        alert(monitor, AlertCode.STORAGE_BUSY, AlertLevel.WARNING, "数据库被其他操作占用"),
    )
    post_from_thread(
        bridge.handle_event,
        alert(monitor, AlertCode.ANALYSIS_FAILED, AlertLevel.ERROR, "Row02 分析失败"),
    )
    stamp = datetime.now(UTC)
    post_from_thread(
        bridge.handle_video_event,
        VideoEvent(VideoEventCode.FROZEN_FRAME, VideoEventLevel.ERROR, "画面卡住", stamp, 0.0),
    )
    post_from_thread(  # 帧率偏低只统计，不弹横幅
        bridge.handle_video_event,
        VideoEvent(VideoEventCode.LOW_FPS, VideoEventLevel.WARNING, "帧率低", stamp, 0.0),
    )
    assert wait_for(qapp, lambda: len(monitor.alerts.active_keys()) >= 3)
    keys = monitor.alerts.active_keys()
    assert {"engine:storage_busy", "engine:analysis_failed", "video:frozen_frame"} <= set(keys)
    assert "video:low_fps" not in keys
    assert monitor.alerts.text_of("engine:analysis_failed") == "Row02 分析失败"
    # 状况恢复（active=False）撤销横幅；同键只保留最新一条
    post_from_thread(
        bridge.handle_video_event,
        VideoEvent(
            VideoEventCode.FROZEN_FRAME, VideoEventLevel.ERROR, "恢复", stamp, 0.0, active=False
        ),
    )
    assert wait_for(qapp, lambda: "video:frozen_frame" not in monitor.alerts.active_keys())
    # 别的试验的告警不显示在这个页面上
    other = EngineAlert("OTHER", stamp, AlertLevel.ERROR, AlertCode.DEVICE_FAULT, "设备故障")
    post_from_thread(bridge.handle_event, other)
    settle(qapp, 8)
    assert "engine:device_fault" not in monitor.alerts.active_keys()


def test_banners_can_be_dismissed_and_are_capped(
    qapp: QApplication, live_window: MainWindow
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    for index in range(7):
        monitor.alerts.post(f"k{index}", "warning", f"告警 {index}")
    assert monitor.alerts.visible_count() == 4  # 其余合并为「另有 N 条告警」
    monitor.alerts.clear_key("k6")
    assert monitor.alerts.visible_count() == 4 and "k6" not in monitor.alerts.active_keys()
    monitor.alerts.clear()
    assert monitor.alerts.active_keys() == [] and not monitor.alerts.isVisibleTo(monitor)


# ---------------------------------------------------------------------------- 停止试验


def test_stop_asks_first_and_needs_the_permission(
    qapp: QApplication,
    live_window: MainWindow,
    live_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    assert wait_for(qapp, lambda: monitor.view_model is not None and monitor.view_model.live)
    assert monitor.stop_button.isVisibleTo(monitor) and monitor.stop_button.isEnabled()
    asked: list[tuple[str, bool]] = []

    def decline(_parent: object, title: str, message: str, **kwargs: object) -> bool:
        asked.append((title, bool(kwargs.get("danger"))))
        return False

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(decline))
    monitor.stop_button.click()
    assert asked == [("停止试验？", True)]
    assert live_context.engine.active_experiment_id == monitor.experiment_id  # 取消后继续运行

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *a, **k: True))
    monitor.stop_button.click()
    assert wait_for(
        qapp,
        lambda: (
            monitor.view_model is not None and monitor.view_model.state is ExperimentState.STOPPED
        ),
        60.0,
    )
    assert live_context.engine.wait(30.0)
    assert wait_for(qapp, lambda: not monitor.stop_button.isVisibleTo(monitor))
    assert monitor.read_only and monitor.badge.text() == "已停止"


def test_the_analyst_sees_a_disabled_stop_button(
    qapp: QApplication, live_window: MainWindow, live_context: AppContext
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    assert live_window.logout()
    sign_in(qapp, live_window, "analyst")
    assert live_window.open_experiment(experiment_id)
    page = live_window.page("monitor")
    assert isinstance(page, MonitorPage)
    assert page.stop_button.isVisibleTo(page) and not page.stop_button.isEnabled()
    assert page.stop_button.toolTip() == "当前角色无此权限"


# ---------------------------------------------------------------------------- 合并刷新


def test_event_floods_are_coalesced_into_few_redraws(
    qapp: QApplication, live_window: MainWindow
) -> None:
    monitor = start_sensitivity(qapp, live_window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    bridge = live_window.bridge
    settle(qapp, 6)
    before = monitor.refresh_count
    events = 0
    stamp = datetime.now(UTC)

    def flood() -> None:
        nonlocal events
        for round_ in range(300):
            for tube in range(1, 18):
                bridge.handle_event(
                    WellUpdated(
                        experiment_id,
                        stamp,
                        1,
                        tube,
                        WellStatus.READING if round_ % 2 else WellStatus.INCUBATING,
                    )
                )
                events += 1
            for row in (1, 2, 3):
                bridge.handle_event(
                    RowCountdown(experiment_id, stamp, row, RowPhase.WAITING, 100.0 - round_)
                )
                events += 1

    post_from_thread(flood)
    started = time.monotonic()
    assert wait_for(
        qapp, lambda: monitor.plate.state(1) in (WellState.READING, WellState.INCUBATING)
    )
    settle(qapp, 20)
    assert events == 300 * 20
    redraws = monitor.refresh_count - before
    assert redraws < events / 20  # 6000 条事件只重绘很少几次
    assert time.monotonic() - started >= 0  # 仅用于说明：不断言时长上限
    assert monitor.table.row_count() == 17
