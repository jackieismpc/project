"""仪器设置页：只读与可编辑、非法值提示、保存与持久化、试验进行中的限制、视频输入和预览。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QFileDialog, QSizePolicy

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.video.image_io import write_image
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.user_settings import VideoInput, settings_file
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.settings_page import SettingsPage
from tests.ui.conftest import settle, sign_in, wait_for


def open_settings(app: QApplication, window: MainWindow, who: str = "admin") -> SettingsPage:
    sign_in(app, window, who)
    assert window.navigate("settings")
    page = window.page("settings")
    assert isinstance(page, SettingsPage)
    assert wait_for(app, lambda: not page.previewing)
    return page


def set_text(page: SettingsPage, name: str, text: str) -> None:
    page.fields[name].edit.setText(text)


def finish_field(page: SettingsPage, name: str) -> None:
    page.fields[name].edit.editingFinished.emit()


def make_images(folder: Path, count: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        image = np.full((360, 640, 3), 90 + i * 20, np.uint8)
        write_image(folder / f"f{i}.png", image)
    return folder


def test_shows_every_setting_with_the_effective_values(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_settings(qapp, window)
    assert page.editable and not page.locked
    assert not hasattr(page, "hint")
    text = {name: field.edit.text() for name, field in page.fields.items()}
    assert text["instrument_no"] == "GI-001" and text["incubation_minutes"] == "60"
    assert "frames_per_side" not in text and "settle_s" not in text
    assert text["temperature_target_c"] == "37" and text["temperature_tolerance_c"] == "1"
    assert text["temperature_hold_s"] == "60" and text["retention_days"] == ""  # 默认不自动删除
    assert text["copyright_holder"] == "厦门鲎试剂生物科技股份有限公司"
    assert page.range_label.text() == "有效温度范围 36 – 38 ℃"
    assert "全部保留" in page.fields["retention_days"].hint.text()
    assert not hasattr(page, "backend_card")
    assert page.runtime_card.isVisibleTo(page)
    assert page.diagnostics_button.isEnabled()
    assert "默认保留" in page.diagnostics_hint.text()
    assert page.runtime_paths["data_dir"].text().endswith("data")
    assert all(page.fields[n].edit.isEnabled() == (n != "copyright_holder") for n in page.fields)


def test_diagnostic_log_size_refreshes_periodically(qapp: QApplication, window: MainWindow) -> None:
    page = open_settings(qapp, window)
    assert wait_for(qapp, lambda: page._capacity_token is None)
    data_dir = page._services.data_dir
    log_dir = data_dir / "diagnostics" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "2026-01-01.log"
    log.write_bytes(b"x" * 1234)
    page._capacity_updated_at = 0
    page._request_diagnostic_capacity()
    assert wait_for(qapp, lambda: "0.001234 MB" in page.diagnostics_hint.text())
    log.write_bytes(b"x" * 2345)
    assert window.navigate("dashboard")
    assert window.navigate("settings")
    assert "0.001234 MB" in page.diagnostics_hint.text()
    page._capacity_updated_at = 0
    page._capacity_timer.timeout.emit()
    assert wait_for(qapp, lambda: "0.002345 MB" in page.diagnostics_hint.text())


def test_diagnostic_capacity_is_read_in_background(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gelinspect.ui.pages import settings_page

    worker_threads: list[int] = []
    original = settings_page._diagnostic_capacity

    def measure(log_dir: Path, data_dir: Path) -> tuple[int, int]:
        worker_threads.append(threading.get_ident())
        return original(log_dir, data_dir)

    monkeypatch.setattr(settings_page, "_diagnostic_capacity", measure)
    page = open_settings(qapp, window)
    assert wait_for(qapp, lambda: page._capacity is not None)
    assert worker_threads and worker_threads[0] != threading.get_ident()


@pytest.mark.parametrize("lang", ["zh_CN", "zh_TW", "en"])
def test_diagnostics_description_can_grow_when_wrapped(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
    lang: str,
) -> None:
    from gelinspect.services.appearance_service import AppearancePreferences
    from gelinspect.ui.theme.appearance import apply_appearance

    language.set_language(Language(lang), persist=False)
    page = open_settings(qapp, window)
    apply_appearance(qapp, AppearancePreferences(font_scale="large"))
    try:
        settle(qapp)
        window.resize(1280, 828)
        settle(qapp)
        hint = page.diagnostics_hint
        metrics = QFontMetrics(hint.font())
        measured = metrics.boundingRect(
            0, 0, hint.width(), 2000, int(Qt.TextFlag.TextWordWrap), hint.text()
        )
        assert hint.wordWrap()
        assert hint.hasHeightForWidth()
        assert hint.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Minimum
        assert hint.height() >= measured.height()
    finally:
        apply_appearance(qapp, AppearancePreferences())
        language.set_language(Language.ZH_CN, persist=False)


def test_appearance_auto_saves_and_failure_restores_controls_without_changing_device_draft(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gelinspect.services.appearance_service import AppearancePreferences
    from gelinspect.ui.theme.appearance import apply_appearance
    from gelinspect.ui.theme.tokens import current_tokens

    appearance_path = window.services.data_dir / "appearance_settings.json"
    initial_appearance_file = appearance_path.read_bytes() if appearance_path.exists() else None
    page = open_settings(qapp, window)
    assert (
        appearance_path.read_bytes() if appearance_path.exists() else None
    ) == initial_appearance_file
    set_text(page, "instrument_no", "UNSAVED-DRAFT")
    before = current_tokens()
    old_preferences = window.services.appearance.get("admin")
    save = window.services.appearance.save
    old_appearance_file = appearance_path.read_bytes() if appearance_path.exists() else None

    def fail_save(_username: str, _preferences: AppearancePreferences) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(window.services.appearance, "save", fail_save)
    page.palette_box.setCurrentIndex(page.palette_box.findData("blue"))
    assert page.palette_box.currentData() == old_preferences.palette
    assert page.font_scale_box.currentData() == old_preferences.font_scale
    assert "保存失败" in page.appearance_message.text()
    assert current_tokens() == before
    assert window.services.appearance.get("admin") == old_preferences
    if old_appearance_file is None:
        assert not appearance_path.exists()
    else:
        assert appearance_path.read_bytes() == old_appearance_file

    monkeypatch.setattr(window.services.appearance, "save", save)
    page.palette_box.setCurrentIndex(page.palette_box.findData("blue"))
    page.font_scale_box.setCurrentIndex(page.font_scale_box.findData("large"))
    assert window.services.appearance.get("admin") == AppearancePreferences(
        palette="blue", font_scale="large"
    )
    assert current_tokens().type.body == 16
    assert current_tokens().colors.primary == "#315F9B"
    assert page.fields["instrument_no"].edit.text() == "UNSAVED-DRAFT"
    assert not settings_file(window.services.data_dir).exists()

    assert window.logout(confirm=False)
    sign_in(qapp, window, "experimenter")
    assert window.navigate("settings")
    other_page = window.page("settings")
    assert isinstance(other_page, SettingsPage)
    assert other_page.palette_box.currentData() == "teal"
    assert other_page.font_scale_box.currentData() == "system"
    apply_appearance(qapp, AppearancePreferences())


def test_the_video_card_offers_only_real_inputs_and_mentions_the_industrial_camera(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_settings(qapp, window)
    options = [page.type_box.itemText(i) for i in range(page.type_box.count())]
    assert options == ["沿用配置文件", "视频文件", "图片文件夹"]  # 没有实时摄像头
    assert "摄像头" not in " ".join(options)
    # 当前生效的视频源已经预览：分辨率和帧率
    assert page.probe_label.text().startswith("1920×1080") and "15 帧/秒" in page.probe_label.text()
    assert page.preview_view.image() is not None
    assert not page.path_edit.isVisibleTo(page)  # 沿用配置文件时没有路径
    page.type_box.setCurrentIndex(page.type_box.findData(VideoInput.FILE.value))
    assert page.path_edit.isVisibleTo(page) and page.browse_button.isEnabled()
    assert "视频文件" in page.path_edit.placeholderText()
    page.type_box.setCurrentIndex(page.type_box.findData(VideoInput.IMAGES.value))
    assert "图片文件夹" in page.path_edit.placeholderText()


@pytest.mark.parametrize("who", ["experimenter", "analyst"])
def test_other_roles_see_a_read_only_page(
    qapp: QApplication, window: MainWindow, app_context: AppContext, who: str
) -> None:
    sign_in(qapp, window, who)
    assert window.navigate("settings")
    page = window.page("settings")
    assert isinstance(page, SettingsPage)
    assert page.personal_card.isVisibleTo(page)
    assert page.palette_box.isEnabled() and page.font_scale_box.isEnabled()
    assert not page.configuration_area.isVisibleTo(page)
    assert not hasattr(page, "save_button") and not hasattr(page, "revert_button")
    assert not hasattr(page, "runtime_save")
    assert not page.previewing
    assert page._saved_draft is None
    with pytest.raises(PermissionDeniedError):
        app_context.settings.current(window.session)


def test_invalid_values_get_a_clear_message_per_field_and_nothing_is_saved(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    set_text(page, "incubation_minutes", "abc")
    set_text(page, "temperature_target_c", "99")
    set_text(page, "retention_days", "1.5")
    set_text(page, "instrument_no", "  ")
    for name in ("incubation_minutes", "temperature_target_c", "retention_days", "instrument_no"):
        finish_field(page, name)
    errors = {n: f.error.text() for n, f in page.fields.items() if f.error.isVisibleTo(page)}
    assert errors == {
        "incubation_minutes": "孵育时间（分钟）必须是数字，范围 0.1–1440",
        "temperature_target_c": "目标温度（℃）应在 20–45 之间",
        "retention_days": "数据保留天数必须是整数，范围 0–36500",
        "instrument_no": "仪器编号不能为空",
    }
    assert "未保存" in page.save_message.text()
    assert not settings_file(app_context.config.data_dir).exists()
    assert app_context.engine.config.incubation_minutes == 60
    # 改正后保存成功，错误提示消失
    fixes = {
        "incubation_minutes": "30",
        "temperature_target_c": "36.5",
        "retention_days": "",
        "instrument_no": "GI-002",
    }
    for name, value in fixes.items():
        set_text(page, name, value)
        finish_field(page, name)
    assert not any(f.error.isVisibleTo(page) for f in page.fields.values())


def test_valid_field_auto_saves_without_invalid_unrelated_draft_overwrite(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    page.type_box.setCurrentIndex(page.type_box.findData(VideoInput.FILE.value))
    assert page.video_input() is VideoInput.FILE and page.path_edit.text() == ""
    set_text(page, "incubation_minutes", "halfway")
    page.fields["incubation_minutes"].edit.editingFinished.emit()
    assert page.fields["incubation_minutes"].error.isVisibleTo(page)
    set_text(page, "instrument_no", "GI-AUTO")
    page.fields["instrument_no"].edit.editingFinished.emit()
    assert app_context.experiments.instrument_no == "GI-AUTO"
    assert app_context.engine.config.incubation_minutes == 60
    assert page.fields["incubation_minutes"].edit.text() == "halfway"
    assert "自动保存" in page.save_message.text()
    assert app_context.config.video.source != VideoInput.FILE.value


def test_a_bad_video_path_is_explained_next_to_the_path(
    qapp: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    page = open_settings(qapp, window)
    page.set_video_input(VideoInput.FILE, str(tmp_path / "missing.mp4"))
    assert page.path_error.isVisibleTo(page) and "路径不存在" in page.path_error.text()
    page.set_video_input(VideoInput.IMAGES, str(tmp_path))
    assert "没有 JPG 或 PNG" in page.path_error.text()


def test_save_persists_audits_and_applies_to_the_runtime(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    window._on_login_submitted("developer", "12345678")
    assert window.navigate("settings")
    page = window.page("settings")
    assert isinstance(page, SettingsPage)
    assert wait_for(qapp, lambda: not page.previewing)
    changes = {
        "instrument_no": "GI-042",
        "incubation_minutes": "45",
        "temperature_hold_s": "120",
        "retention_days": "90",
        "copyright_holder": "厦门某某生物科技股份有限公司",
    }
    for name, value in changes.items():
        set_text(page, name, value)
        finish_field(page, name)
    assert "本项已自动保存" in page.save_message.text()
    assert app_context.engine.config.incubation_minutes == 45
    assert app_context.experiments.instrument_no == "GI-042"
    assert app_context.reports.copyright_holder == "厦门某某生物科技股份有限公司"
    saved = json.loads(settings_file(app_context.config.data_dir).read_text(encoding="utf-8"))
    assert (
        saved["settings"]["retention_days"] == 90 and saved["settings"]["instrument_no"] == "GI-042"
    )
    events = app_context.database.audit.query(
        AuditQuery(action=AuditAction.SETTINGS_CHANGED), PageRequest()
    )
    details = [r.event.detail for r in events.items]
    assert any("仪器编号 GI-001 → GI-042" in detail for detail in details)
    assert any("孵育时间（分钟） 60 → 45" in detail for detail in details)
    # 切页再回来，显示的是自动保存后的值。
    window.navigate("dashboard")
    window.navigate("settings")
    assert page.fields["instrument_no"].edit.text() == "GI-042"


def test_refresh_discards_uncommitted_edit(qapp: QApplication, window: MainWindow) -> None:
    page = open_settings(qapp, window)
    set_text(page, "instrument_no", "NOT-SAVED")
    page.refresh()
    assert page.fields["instrument_no"].edit.text() == "GI-001"


def test_logout_clears_old_session_draft_before_page_hide(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    set_text(page, "instrument_no", "OLD-SESSION-DRAFT")
    page.set_session(None)
    assert page._saved_draft is None and not page.editable
    page._save_pending_fields()
    assert app_context.experiments.instrument_no == "GI-001"
    assert not settings_file(app_context.config.data_dir).exists()


def test_while_an_experiment_runs_only_the_non_impacting_fields_can_be_edited(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_settings(qapp, window)  # 先进入页面，再假装有试验在运行（工作台读不到假试验）
    monkeypatch.setattr(ExperimentEngine, "active_experiment_id", property(lambda _s: "EXP-RUN-1"))
    page.refresh()
    assert page.locked and page.banner.isVisibleTo(page)
    assert "EXP-RUN-1" in page.banner.title.text()
    impacting = {
        "incubation_minutes",
        "temperature_target_c",
        "temperature_tolerance_c",
        "temperature_hold_s",
    }
    for name, field in page.fields.items():
        assert field.edit.isEnabled() is (name not in impacting and name != "copyright_holder"), (
            name
        )
    assert not page.type_box.isEnabled() and not page.path_edit.isEnabled()
    set_text(page, "retention_days", "30")
    finish_field(page, "retention_days")  # 不影响运行的项仍可保存
    set_text(page, "incubation_minutes", "10")  # 绕过置灰也会被服务层拒绝
    finish_field(page, "incubation_minutes")
    assert "正在运行" in page.save_message.text()
    monkeypatch.undo()  # 关闭窗口时不能还“有试验在运行”，否则会弹出退出确认


def test_preview_a_chosen_image_folder_shows_resolution_and_frame_count_and_save_switches_source(
    qapp: QApplication, window: MainWindow, app_context: AppContext, tmp_path: Path
) -> None:
    page = open_settings(qapp, window)
    folder = make_images(tmp_path / "图片")
    page.set_video_input(VideoInput.IMAGES, str(folder))
    page.preview()
    assert page.previewing
    assert wait_for(qapp, lambda: not page.previewing)
    assert page.probe_label.text().startswith("640×360") and "3 帧" in page.probe_label.text()
    image = page.preview_view.image()
    assert image is not None and (image.width(), image.height()) == (640, 360)
    assert app_context.config.video.source == "images" and app_context.config.video.device == str(
        folder
    )
    # 之后进入页面：预览的是当前生效的图片文件夹
    window.navigate("dashboard")
    window.navigate("settings")
    assert wait_for(qapp, lambda: not page.previewing)
    assert page.video_input() is VideoInput.IMAGES and page.path_edit.text() == str(folder)
    assert page.probe_label.text().startswith("640×360")


def test_preview_failure_is_shown_not_raised(
    qapp: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    page = open_settings(qapp, window)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"nope")
    page.set_video_input(VideoInput.FILE, str(broken))
    page.preview()
    assert wait_for(qapp, lambda: not page.previewing)
    assert page.path_error.isVisibleTo(page) and "无法读取视频输入" in page.path_error.text()
    assert page.preview_view.image() is None


def test_browse_fills_the_path_and_previews(
    qapp: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_settings(qapp, window)
    folder = make_images(tmp_path / "pics")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(folder))
    page.set_video_input(VideoInput.IMAGES, "")
    page.browse_button.click()
    assert page.path_edit.text() == str(folder)
    assert wait_for(qapp, lambda: not page.previewing)
    assert page.probe_label.text().startswith("640×360")
    settle(qapp)


def test_diagnostic_export_uses_selected_dates_and_creates_zip(
    qapp: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_settings(qapp, window)
    target = tmp_path / "diagnostic.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    today = QDate.currentDate()
    page.diagnostics_range.setChecked(True)
    page.diagnostics_start.setDate(today.addDays(-1))
    page.diagnostics_end.setDate(today)
    page.diagnostics_button.click()
    assert wait_for(qapp, lambda: page._diagnostic_token is None, 30)
    assert target.is_file()
    assert "诊断包已生成" in page.save_message.text()


def test_diagnostic_export_rejects_inverted_dates(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_settings(qapp, window)
    page.diagnostics_range.setChecked(True)
    today = QDate.currentDate()
    page.diagnostics_start.setDate(today)
    page.diagnostics_end.setDate(today.addDays(-1))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_a, **_k: pytest.fail("unexpected dialog")
    )
    page.diagnostics_button.click()
    assert "开始日期不能晚于结束日期" in page.save_message.text()


def test_camera_exposure_controls_visibility_and_auto_save(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    assert page.camera_exposure_box.isVisibleTo(page)
    assert page.exposure_mode_box.isEnabled()
    assert page.exposure_us_edit.isEnabled()

    # 切换到连续自动曝光时，曝光时间输入框置灰
    page.exposure_mode_box.setCurrentIndex(page.exposure_mode_box.findData("continuous"))
    assert not page.exposure_us_edit.isEnabled()
    assert app_context.config.video.exposure_auto == "continuous"

    # 切回手动并输入有效曝光时间
    page.exposure_mode_box.setCurrentIndex(page.exposure_mode_box.findData("off"))
    assert page.exposure_us_edit.isEnabled()
    page.exposure_us_edit.setText("12000")
    page.exposure_us_edit.editingFinished.emit()
    saved_video = app_context.config.video
    assert saved_video.exposure_auto == "off"
    assert saved_video.exposure_us == 12000.0

    # 切换到视频文件时，隐藏相机曝光控制
    page.type_box.setCurrentIndex(page.type_box.findData(VideoInput.FILE.value))
    assert not page.camera_exposure_box.isVisibleTo(page)
