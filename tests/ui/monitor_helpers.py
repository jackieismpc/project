"""监控与复核测试的公共夹具：加速的真实 SQLite + 模拟后端，通过真实向导启动试验。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext, build_app_context
from gelinspect.domain.entities import User
from gelinspect.domain.enums import Role
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.config import parse_config
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.widgets.app_shell import MemoryShellSettings
from tests.ui.conftest import FIXED_NOW, make_config, settle, wait_for, worker_threads
from tests.ui.wizard_helpers import (
    STEP_SAMPLES,
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    select_tubes,
)

FAST_VIDEO = {"source": "simulation.sim_video:SimVideoSource", "device": "sim",
              "width": 640, "height": 360, "fps": 30}  # fmt: skip
FAST_ENGINE: dict[str, object] = {"incubation_minutes": 0.05, "time_scale": 60}
"""孵育 3 设备秒（真实 0.05 秒）：试验几秒内跑完。"""

FINISHED = (ExperimentState.PENDING_REVIEW, ExperimentState.COMPLETED)


LIVE_ENGINE: dict[str, object] = {"incubation_minutes": 60, "time_scale": 60}
"""默认孵育：每排 60 设备分钟 = 真实 60 秒，测试期间一直处于孵育中。"""


def open_context(tmp_path: Path, engine: dict[str, object] = FAST_ENGINE) -> AppContext:
    """画面小的完整装配（真实 SQLite、模拟后端），数据放在 ``tmp_path/data``。"""
    config = make_config(tmp_path, video=FAST_VIDEO, engine=engine)
    return build_app_context(parse_config(config), clock=lambda: FIXED_NOW)


def close_context(context: AppContext) -> None:
    """有序关闭并确认没有线程残留。"""
    import time

    context.close()
    deadline = time.monotonic() + 8.0
    while worker_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker_threads() == []


def open_window(app: QApplication, context: AppContext, language: LanguageManager) -> MainWindow:
    """用 ``context`` 的主窗口（1280×800，关闭动画）。"""
    win = MainWindow(
        context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    win.resize(1280, 800)
    win.show()
    settle(app)
    return win


def close_window(app: QApplication, win: MainWindow) -> None:
    """关闭主窗口；试验还在运行时先停止，免得关窗弹出确认框。"""
    engine = win.services.engine
    if engine.active_experiment_id is not None:
        engine.stop(User("tester", "tester", "测试", Role.EXPERIMENTER), "测试结束")
        engine.wait(30.0)
    win.bridge.close()
    win.close()
    win.deleteLater()
    settle(app)


@pytest.fixture
def fast_context(tmp_path: Path) -> Iterator[AppContext]:
    """孵育极短、画面小的完整装配，试验几秒内跑完。"""
    context = open_context(tmp_path)
    yield context
    close_context(context)


@pytest.fixture
def fast_window(
    qapp: QApplication, fast_context: AppContext, language: LanguageManager
) -> Iterator[MainWindow]:
    """用 ``fast_context`` 的主窗口。"""
    win = open_window(qapp, fast_context, language)
    yield win
    close_window(qapp, win)


@pytest.fixture
def live_context(tmp_path: Path) -> Iterator[AppContext]:
    """默认孵育时长的完整装配：试验在测试期间一直处于孵育中。"""
    context = open_context(tmp_path, LIVE_ENGINE)
    yield context
    close_context(context)


@pytest.fixture
def live_window(
    qapp: QApplication, live_context: AppContext, language: LanguageManager
) -> Iterator[MainWindow]:
    """用 ``live_context`` 的主窗口。"""
    win = open_window(qapp, live_context, language)
    yield win
    close_window(qapp, win)


def start_sensitivity(
    app: QApplication,
    window: MainWindow,
    tubes: range = range(1, 18),
    template: str = "灵敏度复核 (2λ:1)",
) -> MonitorPage:
    """以实验员身份走完向导并启动试验（默认灵敏度复核 17 管），返回监控页。"""
    page = open_wizard(app, window, "experimenter")
    choose_template(app, page, template)
    click(app, page.template_next)
    pass_reagents(app, page)
    click(app, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES
    select_tubes(app, page, tubes)
    click(app, page.sample_next)
    click(app, page.confirm_start)
    assert wait_for(app, lambda: window.current_route == "monitor", 30.0)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    return monitor


def wait_finished(app: QApplication, context: AppContext, timeout_s: float = 90.0) -> None:
    """等试验跑完（全部排分析完，引擎收尾）。"""

    def done() -> bool:
        snap = context.engine.snapshot()
        return snap is not None and snap.state in FINISHED and not snap.running

    assert wait_for(app, done, timeout_s), context.engine.snapshot()
    assert context.engine.wait(30.0)


def click_well(plate: object, tube_no: int) -> None:
    """真实鼠标点击孔板上的某个孔。"""
    from gelinspect.ui.widgets.plate_grid import PlateGrid

    assert isinstance(plate, PlateGrid)
    center = plate.well_rect(tube_no).center()
    QTest.mouseClick(
        plate, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y()))
    )


Predicate = Callable[[], object]
