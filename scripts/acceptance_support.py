"""验收脚本共用的工具：搭建离屏应用、用真实控件走完一次试验、复核、签署。

``capture_manual_images.py``、``measure_acceptance.py`` 和 ``check_english_ui.py`` 都依赖它。
只在开发机上使用：借用 ``tests/`` 里的向导和监控夹具函数，它们通过真实控件（点击、键入）操作界面。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from gelinspect.app_context import AppContext, build_app_context  # noqa: E402
from gelinspect.domain.enums import Interpretation  # noqa: E402
from gelinspect.services.config import DEFAULT_CONFIG, parse_config  # noqa: E402
from gelinspect.services.reporting.texts import ReportLanguage  # noqa: E402
from gelinspect.ui.i18n import Language, LanguageManager, MemoryLanguageSettings  # noqa: E402
from gelinspect.ui.main_window import MainWindow  # noqa: E402
from gelinspect.ui.theme.stylesheet import apply_theme  # noqa: E402
from gelinspect.ui.widgets.app_shell import MemoryShellSettings  # noqa: E402
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in, wait_for  # noqa: E402
from tests.ui.monitor_helpers import start_sensitivity, wait_finished  # noqa: E402

__all__ = [
    "DEMO_PASSWORDS",
    "Rig",
    "close_rig",
    "finish_and_review",
    "open_rig",
    "run_experiment",
    "settle",
    "sign_in",
    "sign_experiment",
    "wait_for",
]

VIDEO_FAST: dict[str, object] = {
    "source": "simulation.sim_video:SimVideoSource",
    "device": "sim",
    "width": 960,
    "height": 540,
    "fps": 30,
}
"""截图和测量用的视频参数：画面比测试用的大，比默认的小，几秒内跑完一排。"""


@dataclass(slots=True)
class Rig:
    """一套离屏运行的应用。

    Attributes:
        app: ``QApplication``。
        context: 应用装配（数据库、服务、后端）。
        language: 语言管理器。
        window: 主窗口（1280×800）。
    """

    app: QApplication
    context: AppContext
    language: LanguageManager
    window: MainWindow


def open_rig(
    root: Path,
    *,
    incubation_minutes: float = 0.05,
    video: Mapping[str, object] | None = None,
    analyzer_config: Path | None = None,
    demo_accounts: bool = True,
    extra: Mapping[str, object] | None = None,
) -> Rig:
    """搭建应用并显示主窗口。

    Args:
        root: 数据目录的父目录（会创建 ``root/data``）。
        incubation_minutes: 每排孵育的设备分钟数，时间加速 60 倍，0.05 约等于 0.05 秒。
        video: 视频参数，默认 :data:`VIDEO_FAST`。
        analyzer_config: 模拟算法的配置文件，默认用内置参数。
        demo_accounts: False 时去掉初始化钩子，首次启动显示「创建管理员」。
        extra: 追加或覆盖的顶层配置项。

    Returns:
        可用的 :class:`Rig`。
    """
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    apply_theme(app)
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    config["data_dir"] = str(root / "data")
    config["video"] = dict(VIDEO_FAST if video is None else video)
    config["engine"] = {"incubation_minutes": incubation_minutes, "time_scale": 60}
    if analyzer_config is not None:
        config["analysis"] = {**DEFAULT_CONFIG["analysis"], "config_path": str(analyzer_config)}
    if not demo_accounts:
        config.pop("bootstrap", None)
    config.update(extra or {})
    context = build_app_context(parse_config(config))
    language = LanguageManager(app, settings=MemoryLanguageSettings())
    window = MainWindow(
        context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    window.resize(1280, 800)
    window.show()
    settle(app)
    return Rig(app, context, language, window)


def close_rig(rig: Rig) -> None:
    """有序关闭：停止还在跑的试验，关闭窗口和后台线程。"""
    from gelinspect.domain.entities import User
    from gelinspect.domain.enums import Role

    engine = rig.window.services.engine
    if engine.active_experiment_id is not None:
        engine.stop(User("tool", "tool", "tool", Role.EXPERIMENTER), "脚本结束")
        engine.wait(30.0)
    rig.window.bridge.close()
    rig.window.close()
    rig.window.deleteLater()
    settle(rig.app)
    rig.context.close()
    rig.language.set_language(Language.ZH_CN, persist=False)


def run_experiment(rig: Rig, *, wait: bool = True, timeout_s: float = 120.0) -> str:
    """实验员走真实向导启动灵敏度复核（17 管），可选等它跑完。

    Args:
        rig: 应用。
        wait: True 时等试验跑完。
        timeout_s: 等待上限。

    Returns:
        试验标识。
    """
    monitor = start_sensitivity(rig.app, rig.window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    if wait:
        wait_finished(rig.app, rig.context, timeout_s)
    return experiment_id


def finish_and_review(rig: Rig, experiment_id: str) -> int:
    """分析员把所有待复核的管判为阳性（模拟人工确认）。

    Args:
        rig: 应用。
        experiment_id: 试验标识。

    Returns:
        复核了几支管。
    """
    analyst = rig.context.auth.login("analyst", DEMO_PASSWORDS["analyst"])
    stored = rig.context.database.experiments.get(experiment_id)
    assert stored is not None
    pending = list(stored.pending_review_tubes)
    for tube in pending:
        rig.context.reviews.review_tube(
            analyst, experiment_id, tube, Interpretation.POSITIVE, "人工确认为阳性"
        )
    return len(pending)


def sign_experiment(
    rig: Rig, experiment_id: str, language: ReportLanguage = ReportLanguage.ZH_CN
) -> float:
    """分析员签署试验。

    Args:
        rig: 应用。
        experiment_id: 试验标识。
        language: 报告语言。

    Returns:
        签署（渲染、签名、落盘、入库）耗时，秒。
    """
    analyst = rig.context.auth.login("analyst", DEMO_PASSWORDS["analyst"])
    started = time.perf_counter()
    rig.context.reports.sign(analyst, experiment_id, DEMO_PASSWORDS["analyst"], language)
    return time.perf_counter() - started
