"""监控页、单管详情和历史页：中英文、两种窗口尺寸下没有控件超界或文字被截断；英文译文完整。"""

from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from tests.ui.conftest import settle, sign_in, wait_for
from tests.ui.monitor_helpers import (
    start_sensitivity,
    wait_finished,
)
from tests.ui.test_layout_languages import SIZES, overflow_report, use


def loaded(page: MonitorPage) -> bool:
    return len(page.images_tab.loaded_rows()) == 3


def has_images(detail: TubeDetailPage) -> bool:
    return detail.images is not None


def test_monitor_tube_and_history_pages_fit_in_both_languages(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    language: LanguageManager,
) -> None:
    window = fast_window
    monitor = start_sensitivity(qapp, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    wait_finished(qapp, fast_context)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    assert window.logout()
    sign_in(qapp, window, "analyst")
    pending = fast_context.database.experiments.get(experiment_id).pending_review_tubes  # type: ignore[union-attr]
    for lang in (Language.ZH_CN, Language.ZH_TW, Language.EN):
        for size in SIZES:
            use(window, language, lang, size)
            assert window.open_experiment(experiment_id)
            page = window.page("monitor")
            assert isinstance(page, MonitorPage)
            for tab in range(4):
                page.tabs.set_current(tab)
                if tab == 1:
                    assert wait_for(qapp, partial(loaded, page), 30.0)
                settle(qapp)
                report = overflow_report(window)
                assert report == [], f"监控页标签 {tab} / {lang.value} / {size}：{report}"
            page.tabs.set_current(0)
            assert window.open_tube(experiment_id, pending[0])
            detail = window.page("tube")
            assert isinstance(detail, TubeDetailPage)
            assert wait_for(qapp, partial(has_images, detail), 30.0)
            detail.review_error.setText("复核意见必填" * 4)
            detail.review_error.setVisible(True)
            detail.sequence_box.setVisible(True)
            report = overflow_report(window)
            assert report == [], f"单管详情 / {lang.value} / {size}：{report}"
            detail.sequence_box.setVisible(False)
            window.navigate("history")
            assert overflow_report(window) == [], f"历史 / {lang.value} / {size}"
            window.navigate("dashboard")
            assert overflow_report(window) == [], f"工作台 / {lang.value} / {size}"


def test_new_pages_speak_english_when_switched(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    language: LanguageManager,
) -> None:
    window = fast_window
    monitor = start_sensitivity(qapp, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    wait_finished(qapp, fast_context)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    language.set_language(Language.EN, persist=False)
    settle(qapp, 8)
    assert monitor.stop_button.text() == "Stop experiment"
    assert [monitor.tabs._bar.tabText(i) for i in range(4)] == [  # noqa: SLF001
        "Experiment data",
        "Images",
        "Report preview",
        "Batch report",
    ]
    assert monitor.badge.text().startswith("Pending review")
    assert monitor.table_card._title.text() == "Experiment data"  # type: ignore[union-attr]  # noqa: SLF001
    assert window.open_tube(experiment_id, 1)
    detail = window.page("tube")
    assert isinstance(detail, TubeDetailPage)
    assert detail.mask_toggle.text() == "Show algorithm masks"
    assert detail.positive_button.text() == "Mark positive"
    assert detail.title.text() == "Tube 1"
    language.set_language(Language.ZH_CN, persist=False)
    settle(qapp, 8)
    assert detail.title.text() == "1 号管" and detail.positive_button.text() == "判为阳性"
