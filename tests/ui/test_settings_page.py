"""仪器设置页：只读与可编辑、非法值提示、保存与持久化、试验进行中的限制、视频输入和预览。"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.user_settings import VideoInput, settings_file
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


def make_images(folder: Path, count: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        image = np.full((360, 640, 3), 90 + i * 20, np.uint8)
        assert cv2.imwrite(str(folder / f"f{i}.png"), image)
    return folder


def test_shows_every_setting_with_the_effective_values(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_settings(qapp, window)
    assert page.editable and not page.locked
    text = {name: field.edit.text() for name, field in page.fields.items()}
    assert text["instrument_no"] == "GI-001" and text["incubation_minutes"] == "60"
    assert text["frames_per_side"] == "3" and text["settle_s"] == "1"
    assert text["temperature_target_c"] == "37" and text["temperature_tolerance_c"] == "1"
    assert text["temperature_hold_s"] == "60" and text["retention_days"] == ""  # 默认不自动删除
    assert text["copyright_holder"] == "[版权方名称]"
    assert page.range_label.text() == "有效温度范围 36 – 38 ℃"
    assert "自动删除" in page.fields["retention_days"].hint.text()
    # 算法后端信息只读展示
    backend = {k: v.text() for k, (_n, v) in page.backend_labels.items()}
    assert backend["backend"].endswith("MockAnalyzer")
    assert backend["model"] == "第一次试验后显示" and backend["config"] == "第一次试验后显示"
    assert all(page.fields[n].edit.isEnabled() for n in page.fields)


def test_the_video_card_offers_only_real_inputs_and_mentions_the_industrial_camera(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_settings(qapp, window)
    options = [page.type_box.itemText(i) for i in range(page.type_box.count())]
    assert options == ["沿用配置文件", "视频文件", "图片文件夹"]  # 没有实时摄像头
    assert "摄像头" not in " ".join(options)
    assert page.camera_note.text() == "工业相机：型号确定后接入。"
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
    page = open_settings(qapp, window, who)
    assert not page.editable and page.readonly_badge.isVisibleTo(page)
    assert page.readonly_badge.text() == "只读"
    assert all(not f.edit.isEnabled() for f in page.fields.values())
    assert not page.type_box.isEnabled() and not page.save_button.isEnabled()
    assert not page.browse_button.isEnabled() and not page.preview_button.isEnabled()
    assert page.banner.isVisibleTo(page) and "管理员" in page.banner.detail.text()
    assert page.fields["instrument_no"].edit.text() == "GI-001"  # 仍能看到当前值
    # 服务层也拒绝（界面被绕过时）
    assert page.save() is False
    assert not settings_file(app_context.config.data_dir).exists()
    assert not app_context.database.audit.query(
        AuditQuery(action=AuditAction.SETTINGS_CHANGED), PageRequest()
    ).total


def test_invalid_values_get_a_clear_message_per_field_and_nothing_is_saved(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    set_text(page, "incubation_minutes", "abc")
    set_text(page, "frames_per_side", "2")
    set_text(page, "temperature_target_c", "99")
    set_text(page, "retention_days", "1.5")
    set_text(page, "instrument_no", "  ")
    assert page.save() is False
    errors = {n: f.error.text() for n, f in page.fields.items() if f.error.isVisibleTo(page)}
    assert errors == {
        "incubation_minutes": "孵育时间（分钟）必须是数字，范围 0.1–1440",
        "frames_per_side": "翻转前后各取帧数应在 3–20 之间",
        "temperature_target_c": "目标温度（℃）应在 20–45 之间",
        "retention_days": "数据保留天数必须是整数，范围 1–36500",
        "instrument_no": "仪器编号不能为空",
    }
    assert "5 项设置不合法，没有保存" in page.save_message.text()
    assert not settings_file(app_context.config.data_dir).exists()
    assert app_context.engine.config.incubation_minutes == 60
    # 改正后保存成功，错误提示消失
    fixes = {
        "incubation_minutes": "30",
        "frames_per_side": "4",
        "temperature_target_c": "36.5",
        "retention_days": "",
        "instrument_no": "GI-002",
    }
    for name, value in fixes.items():
        set_text(page, name, value)
    assert page.save() is True
    assert not any(f.error.isVisibleTo(page) for f in page.fields.values())


def test_a_bad_video_path_is_explained_next_to_the_path(
    qapp: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    page = open_settings(qapp, window)
    page.set_video_input(VideoInput.FILE, str(tmp_path / "missing.mp4"))
    assert page.save() is False
    assert page.path_error.isVisibleTo(page) and "路径不存在" in page.path_error.text()
    page.set_video_input(VideoInput.IMAGES, str(tmp_path))
    assert page.save() is False and "没有 JPG 或 PNG" in page.path_error.text()


def test_save_persists_audits_and_applies_to_the_runtime(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_settings(qapp, window)
    set_text(page, "instrument_no", "GI-042")
    set_text(page, "incubation_minutes", "45")
    set_text(page, "temperature_hold_s", "120")
    set_text(page, "retention_days", "90")
    set_text(page, "copyright_holder", "厦门某某生物科技股份有限公司")
    assert page.save() is True
    assert "已保存" in page.save_message.text()
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
    (detail,) = [r.event.detail for r in events.items]
    assert "仪器编号 GI-001 → GI-042" in detail and "孵育时间（分钟） 60 → 45" in detail
    # 切页再回来，显示的是保存后的值；保存没有改动时提示「没有改动」
    window.navigate("dashboard")
    window.navigate("settings")
    assert page.fields["instrument_no"].edit.text() == "GI-042"
    assert page.save() is True and page.save_message.text() == "没有改动。"


def test_revert_discards_unsaved_edits(qapp: QApplication, window: MainWindow) -> None:
    page = open_settings(qapp, window)
    set_text(page, "instrument_no", "NOT-SAVED")
    page.revert_button.click()
    assert page.fields["instrument_no"].edit.text() == "GI-001"


def test_while_an_experiment_runs_only_the_non_impacting_fields_can_be_edited(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_settings(qapp, window)  # 先进入页面，再假装有试验在运行（工作台读不到假试验）
    monkeypatch.setattr(ExperimentEngine, "active_experiment_id", property(lambda _s: "EXP-RUN-1"))
    page.refresh()
    assert page.locked and page.banner.isVisibleTo(page)
    assert "EXP-RUN-1" in page.banner.title.text()
    impacting = {"incubation_minutes", "frames_per_side", "settle_s", "temperature_target_c",
                 "temperature_tolerance_c", "temperature_hold_s"}  # fmt: skip
    for name, field in page.fields.items():
        assert field.edit.isEnabled() is (name not in impacting), name
    assert not page.type_box.isEnabled() and not page.path_edit.isEnabled()
    set_text(page, "retention_days", "30")
    assert page.save() is True  # 不影响运行的项仍可保存
    set_text(page, "incubation_minutes", "10")  # 绕过置灰也会被服务层拒绝
    assert page.save() is False and "正在运行" in page.save_message.text()
    monkeypatch.undo()  # 关闭窗口时不能还「有试验在运行」，否则会弹出退出确认


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
    assert page.save() is True
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
