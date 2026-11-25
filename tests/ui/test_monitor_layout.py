"""监控页、单管详情和历史页：中英文、两种窗口尺寸下没有控件超界或文字被截断；英文译文完整。"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog
from pytest import MonkeyPatch

from gelinspect.app_context import AppContext
from gelinspect.services.appearance_service import AppearancePreferences
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from gelinspect.ui.theme.appearance import apply_appearance
from gelinspect.ui.widgets.video_view import VideoView
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
    monkeypatch: MonkeyPatch,
) -> None:
    window = fast_window
    monitor = start_sensitivity(qapp, window)
    experiment_id = monitor.experiment_id
    assert experiment_id is not None
    wait_finished(qapp, fast_context)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    assert window.logout()
    sign_in(qapp, window, "analyst")
    for lang in (Language.ZH_CN, Language.ZH_TW, Language.EN):
        for size in SIZES:
            use(window, language, lang, size)
            assert window.open_experiment(experiment_id)
            page = window.page("monitor")
            assert isinstance(page, MonitorPage)
            for tab in range(3):
                page.tabs.set_current(tab)
                if tab == 1:
                    assert wait_for(qapp, partial(loaded, page), 30.0)
                settle(qapp)
                report = overflow_report(window)
                assert report == [], f"监控页标签 {tab} / {lang.value} / {size}：{report}"
            page.tabs.set_current(0)
            assert window.open_tube(experiment_id, 1)
            detail = window.page("tube")
            assert isinstance(detail, TubeDetailPage)
            assert wait_for(qapp, partial(has_images, detail), 30.0)
            assert len(detail.sequence_views) == 6
            assert detail.sequence_caption.isVisible()
            assert all(view.isVisible() for view in detail.sequence_views)
            detail.review_error.setText("复核意见必填" * 4)
            detail.review_error.setVisible(True)
            assert detail.sequence_box.isVisible()
            report = overflow_report(window)
            assert report == [], f"单管详情 / {lang.value} / {size}：{report}"
            if lang is Language.ZH_CN and size == SIZES[0]:
                apply_appearance(qapp, AppearancePreferences(font_scale="large"))
                window.resize(1280, 828)
                settle(qapp)
                assert detail.scroll_area.verticalScrollBar().maximum() == 0
                assert all(view.isVisible() for view in detail.sequence_views)
                assert detail.sequence_caption.isVisible()
                window.resize(1024, 700)
                settle(qapp)
                assert (
                    detail.scroll_area.verticalScrollBarPolicy()
                    != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                assert all(view.isVisible() for view in detail.sequence_views)
                apply_appearance(qapp, AppearancePreferences())

                previous_dialog_count = len(detail.findChildren(QDialog))
                image = QImage(16, 16, QImage.Format.Format_ARGB32)
                zoom_dialogs: list[QDialog] = []
                zoom_views: list[VideoView | None] = []

                def finish_zoom(
                    dialog: QDialog,
                    dialogs: list[QDialog] = zoom_dialogs,
                    views: list[VideoView | None] = zoom_views,
                ) -> int:
                    dialogs.append(dialog)
                    view = dialog.findChild(VideoView)
                    assert view is not None and view.image() is not None
                    views.append(view)
                    return int(QDialog.DialogCode.Accepted)

                with monkeypatch.context() as patch:
                    patch.setattr(QDialog, "exec", finish_zoom)
                    detail._show_zoom(image, "测试图像")
                assert zoom_views[0] is not None and zoom_views[0].image() is None
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                assert len(detail.findChildren(QDialog)) == previous_dialog_count
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
    assert [monitor.tabs._bar.tabText(i) for i in range(3)] == [  # noqa: SLF001
        "Experiment data",
        "Images",
        "Report preview",
    ]
    assert monitor.badge.text() == "Completed"
    assert monitor.table_card._title.text() == "Experiment data"  # type: ignore[union-attr]  # noqa: SLF001
    assert window.open_tube(experiment_id, 1)
    detail = window.page("tube")
    assert isinstance(detail, TubeDetailPage)
    assert detail.mask_toggle.isHidden() and not detail.mask_overlay_visible()
    assert detail.sequence_box.isVisible()
    assert detail.positive_button.text() == "Mark positive"
    assert detail.title.text() == "Tube 1"
    language.set_language(Language.ZH_CN, persist=False)
    settle(qapp, 8)
    assert detail.title.text() == "1 号管" and detail.positive_button.text() == "判为阳性"
