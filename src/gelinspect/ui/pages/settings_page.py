"""统一设置页：个人外观对所有账号开放，设备配置按权限提供查看和修改。

查看设备配置需要 ``settings_view``，修改需要 ``instrument_configure``。
实验进行中，影响运行的项（孵育、读数、温度、视频输入）置灰，其余项仍可改。
校验和保存都在服务层：非法值逐项提示原因，一项都不会写；保存后写审计（旧值和新值），
改动保存在数据目录的用户配置文件里，不改仓库里的默认配置，对之后启动的试验生效。
视频输入支持视频文件和图片文件夹。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressDialog,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.appearance_service import (
    AppearancePreferences,
    AppearanceSettingsError,
)
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.diagnostics_service import (
    DiagnosticCancelledError,
    DiagnosticPackageResult,
    diagnostic_cancel_token,
)
from gelinspect.services.errors import ServiceError
from gelinspect.services.settings_service import SettingsError, SettingsView, VideoProbe
from gelinspect.services.user_settings import (
    IMPACTING_FIELDS,
    SettingsDraft,
    SettingsProblem,
    VideoInput,
)
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_settings_problem,
    describe_storage_busy,
    setting_label,
)
from gelinspect.ui.pages.page_frame import build_page_column
from gelinspect.ui.pages.wizard_widgets import Banner
from gelinspect.ui.theme.appearance import apply_appearance
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.date_edit import OptionalDateEdit
from gelinspect.ui.widgets.imaging import bgr_to_qimage
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind
from gelinspect.ui.widgets.video_view import VideoView

_log = logging.getLogger(__name__)
_CAPACITY_REFRESH_SECONDS = 60


def _diagnostic_capacity(log_dir: Path, data_dir: Path) -> tuple[int, int]:
    """在后台读取日志大小及数据盘可用空间。"""
    log_bytes = sum(
        path.stat().st_size
        for path in (
            *log_dir.glob("gelinspect.log*"),
            *(data_dir / "diagnostics" / "logs").glob("*.log"),
        )
        if path.is_file() and not path.is_symlink()
    )
    return log_bytes, shutil.disk_usage(data_dir).free


_TEXT_FIELDS = (
    "instrument_no",
    "incubation_minutes",
    "temperature_target_c",
    "temperature_tolerance_c",
    "temperature_hold_s",
    "retention_days",
    "copyright_holder",
)


class _DiagnosticProgress(QObject):
    updated = Signal(int, int)


class _Field(QWidget):
    """一个设置项：标签、输入框和这一项的错误提示。"""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = Label("", "lab")
        self.edit = QLineEdit()
        self.edit.setMinimumHeight(44)
        self.hint = Label("", "meta")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        self.error = Label("", "danger")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        for widget in (self.label, self.edit, self.hint, self.error):
            layout.addWidget(widget)

    def set_error(self, text: str) -> None:
        """显示或清除这一项的错误提示。"""
        self.error.setText(text)
        self.error.setVisible(bool(text))
        self.edit.setProperty("invalid", bool(text))

    def set_hint(self, text: str) -> None:
        """显示或清除这一项的说明。"""
        self.hint.setText(text)
        self.hint.setVisible(bool(text))


class SettingsPage(QWidget):
    """仪器设置页。"""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._view: SettingsView | None = None
        self._editable = False
        self._locked = False
        self._loading_settings = False
        self._saved_draft: SettingsDraft | None = None
        self._loading_appearance = False
        self._appearance_preferences = AppearancePreferences()
        self._preview_token: int | None = None
        self._previewing = False
        self._runner = BackgroundRunner(self)
        self._capacity_token: int | None = None
        self._capacity: tuple[int, int] | None = None
        self._capacity_updated_at = 0.0
        self._capacity_timer = QTimer(self)
        self._capacity_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._capacity_timer.setInterval(_CAPACITY_REFRESH_SECONDS * 1000)
        self._capacity_timer.timeout.connect(self._request_diagnostic_capacity)
        self._diagnostic_cancel = diagnostic_cancel_token()
        self._diagnostic_progress: QProgressDialog | None = None
        self._diagnostic_relay = _DiagnosticProgress(self)
        self._diagnostic_relay.updated.connect(self._update_diagnostic_progress)
        self._diagnostic_token: int | None = None
        self.fields: dict[str, _Field] = {name: _Field(name) for name in _TEXT_FIELDS}

        column, _scroll = build_page_column(self)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.readonly_badge = StatusBadge("", BadgeTone.NEUTRAL)
        top.addWidget(self.title)
        top.addWidget(self.readonly_badge)
        top.addStretch(1)
        column.addLayout(top)

        self.banner = Banner()
        column.addWidget(self.banner)

        self.personal_card = Card(" ")
        self.palette_label = Label("", "lab")
        self.palette_box = QComboBox()
        self.palette_box.setMinimumHeight(44)
        self.palette_box.addItem("", "teal")
        self.palette_box.addItem("", "blue")
        self.font_scale_label = Label("", "lab")
        self.font_scale_box = QComboBox()
        self.font_scale_box.setMinimumHeight(44)
        for scale in ("system", "small", "large"):
            self.font_scale_box.addItem("", scale)
        self.appearance_message = Label("", "meta")
        self.appearance_message.setWordWrap(True)
        self.palette_box.currentIndexChanged.connect(self._on_appearance_changed)
        self.font_scale_box.currentIndexChanged.connect(self._on_appearance_changed)
        self.personal_card.body.addWidget(self.palette_label)
        self.personal_card.body.addWidget(self.palette_box)
        self.personal_card.body.addWidget(self.font_scale_label)
        self.personal_card.body.addWidget(self.font_scale_box)
        self.personal_card.body.addWidget(self.appearance_message)
        column.addWidget(self.personal_card)

        self.configuration_area = QWidget(self)
        work = QHBoxLayout(self.configuration_area)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(14)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.instrument_card = Card(" ")
        for name in ("instrument_no", "copyright_holder", "retention_days"):
            self.instrument_card.body.addWidget(self.fields[name])
        self.encrypt_box = QCheckBox()
        self.encrypt_box.setMinimumHeight(44)
        self.encrypt_hint = Label("", "meta")
        self.encrypt_hint.setWordWrap(True)
        self.instrument_card.body.addWidget(self.encrypt_box)
        self.instrument_card.body.addWidget(self.encrypt_hint)
        self.instrument_card.body.addStretch(1)
        grid.addWidget(self.instrument_card, 0, 0)

        self.run_card = Card(" ")
        for name in ("incubation_minutes",):
            self.run_card.body.addWidget(self.fields[name])
        self.run_card.body.addStretch(1)
        grid.addWidget(self.run_card, 1, 0)

        self.temperature_card = Card(" ")
        for name in ("temperature_target_c", "temperature_tolerance_c"):
            self.temperature_card.body.addWidget(self.fields[name])
        self.range_label = Label("", "body", mono=True)
        self.temperature_card.body.addWidget(self.range_label)
        self.temperature_card.body.addWidget(self.fields["temperature_hold_s"])
        self.temperature_card.body.addStretch(1)
        grid.addWidget(self.temperature_card, 2, 0)

        self.video_card = Card(" ")
        self.type_label = Label("", "lab")
        self.type_box = QComboBox()
        self.type_box.setMinimumHeight(44)
        for kind in VideoInput:
            self.type_box.addItem("", kind.value)
        self.video_card.body.addWidget(self.type_label)
        self.video_card.body.addWidget(self.type_box)
        self.path_label = Label("", "lab")
        self.path_error = Label("", "danger")
        self.path_error.setWordWrap(True)
        self.path_error.setVisible(False)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setMinimumHeight(44)
        self.browse_button = Button("")
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        self.video_card.body.addWidget(self.path_label)
        self.video_card.body.addLayout(path_row)
        self.video_card.body.addWidget(self.path_error)
        probe_row = QHBoxLayout()
        probe_row.setSpacing(12)
        self.preview_button = Button("")
        self.probe_label = Label("", "body", mono=True)
        self.probe_label.setWordWrap(True)
        probe_row.addWidget(self.preview_button)
        probe_row.addWidget(self.probe_label, 1)
        self.video_card.body.addLayout(probe_row)
        self.preview_view = VideoView(min_height=220, max_height=300)
        self.video_card.body.addWidget(self.preview_view)
        self.video_card.body.addStretch(1)
        grid.addWidget(self.video_card, 0, 1)

        self.runtime_card = Card(" ")
        self.runtime_paths: dict[str, QLineEdit] = {}
        self.runtime_browse: dict[str, Button] = {}
        for key in ("config_path", "data_dir", "analysis_config_path"):
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setMinimumHeight(40)
            button = Button("")
            row.addWidget(edit, 1)
            row.addWidget(button)
            self.runtime_card.body.addLayout(row)
            self.runtime_paths[key] = edit
            self.runtime_browse[key] = button
            button.clicked.connect(lambda _checked=False, name=key: self.browse_runtime_path(name))
            edit.editingFinished.connect(self.save_runtime_selection)
        self.runtime_hint = Label("", "meta")
        self.runtime_hint.setWordWrap(True)
        self.runtime_card.body.addWidget(self.runtime_hint)
        self.runtime_card.body.addStretch(1)
        grid.addWidget(self.runtime_card, 1, 1)

        self.diagnostics_card = Card(" ")
        self.diagnostics_hint = Label("", "meta")
        self.diagnostics_hint.setWordWrap(True)
        self.diagnostics_hint.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.diagnostics_range = QCheckBox()
        self.diagnostics_start_label = Label("", "lab")
        self.diagnostics_end_label = Label("", "lab")
        self.diagnostics_start = OptionalDateEdit()
        self.diagnostics_end = OptionalDateEdit()
        self.diagnostics_range.toggled.connect(self._apply_enabled)
        dates = QHBoxLayout()
        for widget in (
            self.diagnostics_start_label,
            self.diagnostics_start,
            self.diagnostics_end_label,
            self.diagnostics_end,
        ):
            dates.addWidget(widget)
        self.diagnostics_button = Button("", ButtonVariant.SECONDARY)
        self.diagnostics_card.body.addWidget(self.diagnostics_hint)
        self.diagnostics_card.body.addWidget(self.diagnostics_range)
        self.diagnostics_card.body.addLayout(dates)
        self.diagnostics_card.body.addWidget(self.diagnostics_button)
        self.diagnostics_card.body.addStretch(1)
        self.diagnostics_button.clicked.connect(self.export_diagnostics)
        grid.addWidget(self.diagnostics_card, 2, 1)

        work.addLayout(grid)
        column.addWidget(self.configuration_area)

        self.save_message = Label("", "meta")
        self.save_message.setWordWrap(True)
        grid.addWidget(self.save_message, 3, 0, 1, 2)
        column.addStretch(1)

        for name in _TEXT_FIELDS:
            self.fields[name].edit.editingFinished.connect(
                lambda field=name: self._save_fields({field})
            )
        self.encrypt_box.toggled.connect(lambda _checked: self._save_fields({"encrypt_exports"}))
        self.type_box.currentIndexChanged.connect(self._on_video_type_changed)
        self.path_edit.editingFinished.connect(
            lambda: self._save_fields({"video_input", "video_path"})
        )
        self.browse_button.clicked.connect(self.browse)
        self.preview_button.clicked.connect(self.preview)
        for name in ("temperature_target_c", "temperature_tolerance_c"):
            self.fields[name].edit.textChanged.connect(self._update_range)
        self.retranslate()

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    @property
    def editable(self) -> bool:
        """当前用户能否修改设置（``instrument_configure``）。"""
        return self._editable

    @property
    def locked(self) -> bool:
        """是否因为试验进行中，影响运行的项暂时不能改。"""
        return self._locked

    @property
    def previewing(self) -> bool:
        """是否有视频预览在后台读取。"""
        return self._previewing

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """等后台任务跑完（不处理回调），关闭窗口和测试清理用。

        Args:
            timeout_ms: 时限。

        Returns:
            是否已全部结束。
        """
        return self._runner.wait(timeout_ms)

    def refresh(self) -> None:
        """重新读取生效的设置、后端信息，并预览当前视频源。"""
        # 刷新失败、注销或切换账号时，不能让旧会话的输入成为下一会话的保存基线。
        self._saved_draft = None
        self._view = None
        self._editable = False
        self._locked = False
        session = self._session
        if session is None:
            self._capacity_timer.stop()
            self.personal_card.hide()
            self.configuration_area.hide()
            return
        self.personal_card.show()
        self._load_appearance(session)
        can_view_settings = session.can(Permission.SETTINGS_VIEW)
        self.configuration_area.setVisible(can_view_settings)
        if not can_view_settings:
            self._capacity_timer.stop()
            # 普通账号仍能使用个人外观偏好；设备设置服务继续独立执行权限校验。
            self._view = None
            self._editable = False
            self._locked = False
            self.banner.clear()
            self.readonly_badge.hide()
            return
        try:
            self._view = self._services.settings.current(session)
        except (AuthError, StorageBusyError) as error:
            self._fail(error)
            self._apply_enabled()
            return
        view = self._view
        self._editable = session.can(Permission.INSTRUMENT_CONFIGURE)
        self._locked = view.running_experiment is not None
        draft = SettingsDraft.from_settings(view.settings)
        if self._editable:
            self._saved_draft = draft
        self._loading_settings = True
        try:
            self._fill(draft)
        finally:
            self._loading_settings = False
        self._update_diagnostic_dates(view.settings.retention_days)
        self._request_diagnostic_capacity()
        self._capacity_timer.start()
        self._clear_errors()
        self.save_message.setText("")
        self._fill_runtime_selection()
        self._apply_enabled()
        self.preview_current()

    def _load_appearance(self, session: Session) -> None:
        """读取账号偏好，避免控件初始化触发自动保存。"""
        try:
            preferences = self._services.appearance.get(session.user.username)
        except (OSError, AppearanceSettingsError):
            _log.exception("APPEARANCE_READ_FAILED")
            preferences = AppearancePreferences()
            self.appearance_message.setText(self.tr("无法读取外观设置。"))
        else:
            self.appearance_message.setText("")
        self._appearance_preferences = preferences
        self._loading_appearance = True
        try:
            self.palette_box.setCurrentIndex(self.palette_box.findData(preferences.palette))
            self.font_scale_box.setCurrentIndex(
                self.font_scale_box.findData(preferences.font_scale)
            )
        finally:
            self._loading_appearance = False

    def _on_appearance_changed(self, _index: int = -1) -> None:
        """选择配色或字号后立即保存并应用；失败时恢复上次保存值。"""
        if self._loading_appearance:
            return
        session = self._session
        if session is None:
            return
        preferences = AppearancePreferences(
            palette=str(self.palette_box.currentData()),
            font_scale=str(self.font_scale_box.currentData()),
        )
        if preferences == self._appearance_preferences:
            return
        try:
            self._services.appearance.save(session.user.username, preferences)
        except (OSError, AppearanceSettingsError):
            _log.exception("APPEARANCE_SAVE_FAILED")
            self._loading_appearance = True
            try:
                self.palette_box.setCurrentIndex(
                    self.palette_box.findData(self._appearance_preferences.palette)
                )
                self.font_scale_box.setCurrentIndex(
                    self.font_scale_box.findData(self._appearance_preferences.font_scale)
                )
            finally:
                self._loading_appearance = False
            self.appearance_message.setText(self.tr("外观设置保存失败，已恢复上次设置。"))
            return
        self._appearance_preferences = preferences
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_appearance(app, preferences)
        self.appearance_message.setText("")

    def draft(self) -> SettingsDraft:
        """按输入框当前内容生成草稿（保存前由服务层校验）。

        Returns:
            草稿。
        """
        text = {name: field.edit.text() for name, field in self.fields.items()}
        return SettingsDraft(
            instrument_no=text["instrument_no"],
            incubation_minutes=text["incubation_minutes"],
            temperature_target_c=text["temperature_target_c"],
            temperature_tolerance_c=text["temperature_tolerance_c"],
            temperature_hold_s=text["temperature_hold_s"],
            video_input=self.video_input(),
            video_path=self.path_edit.text(),
            retention_days=text["retention_days"],
            copyright_holder=text["copyright_holder"],
            encrypt_exports=self.encrypt_box.isChecked(),
        )

    def video_input(self) -> VideoInput:
        """选中的视频输入类型。

        Returns:
            类型。
        """
        return VideoInput(self.type_box.currentData())

    def set_video_input(self, kind: VideoInput, path: str = "") -> None:
        """更新视频输入控件；完整有效的选择会立即自动保存。

        Args:
            kind: 类型。
            path: 路径。
        """
        self.type_box.blockSignals(True)
        self.type_box.setCurrentIndex(self.type_box.findData(kind.value))
        self.type_box.blockSignals(False)
        self.path_edit.setText(path)
        self._on_type_changed()
        if kind is VideoInput.CONFIG or path.strip():
            self._save_fields({"video_input", "video_path"})

    def _fill(self, draft: SettingsDraft) -> None:
        for name, field in self.fields.items():
            field.edit.setText(getattr(draft, name))
        self.type_box.blockSignals(True)
        self.type_box.setCurrentIndex(self.type_box.findData(draft.video_input.value))
        self.type_box.blockSignals(False)
        self.path_edit.setText(draft.video_path)
        self.encrypt_box.setChecked(draft.encrypt_exports)
        self._on_type_changed()
        self._update_range()

    def _fill_runtime_selection(self) -> None:
        selection = self._services.settings.runtime_selection()
        for key, edit in self.runtime_paths.items():
            edit.setText(selection[key])

    def _apply_enabled(self) -> None:
        editable = self._editable
        for name, field in self.fields.items():
            impacting = name in IMPACTING_FIELDS
            field.edit.setEnabled(
                editable
                and not (impacting and self._locked)
                and (
                    name != "copyright_holder"
                    or (
                        self._session is not None
                        and self._session.can(Permission.COMPANY_CONFIGURE)
                    )
                )
            )
        self.encrypt_box.setEnabled(editable)
        video_enabled = editable and not self._locked
        self.type_box.setEnabled(video_enabled)
        self.path_edit.setEnabled(video_enabled and self.video_input() is not VideoInput.CONFIG)
        self.browse_button.setEnabled(video_enabled and self.video_input() is not VideoInput.CONFIG)
        self.preview_button.setEnabled(editable and not self._previewing)
        self.diagnostics_button.setEnabled(
            self._session is not None
            and self._session.can(Permission.DIAGNOSTICS_EXPORT)
            and self._diagnostic_token is None
        )
        range_enabled = self.diagnostics_button.isEnabled() and self.diagnostics_range.isChecked()
        self.diagnostics_range.setEnabled(self.diagnostics_button.isEnabled())
        self.diagnostics_start.setEnabled(range_enabled)
        self.diagnostics_end.setEnabled(range_enabled)
        for edit in self.runtime_paths.values():
            edit.setEnabled(editable and not self._locked)
        for button in self.runtime_browse.values():
            button.setEnabled(editable and not self._locked)
        self.readonly_badge.setVisible(not editable)
        view = self._view
        if not editable:
            self.banner.show_message(
                "info",
                "info",
                self.tr("只读"),
                self.tr("当前账号只能查看设置，修改需要仪器与维护权限。"),
            )
        elif self._locked and view is not None:
            self.banner.show_message(
                "warning",
                "triangle-alert",
                self.tr("实验 {id} 正在运行").format(id=view.running_experiment),
                self.tr(
                    "孵育、读数、温度和视频输入暂时不能修改；仪器编号、数据保留天数和版权方名称仍可修改。"
                ),
            )
        else:
            self.banner.clear()

    def _update_range(self) -> None:
        try:
            target = float(self.fields["temperature_target_c"].edit.text())
            tolerance = float(self.fields["temperature_tolerance_c"].edit.text())
        except ValueError:
            self.range_label.setText("")
            return
        self.range_label.setText(
            self.tr("有效温度范围 {low:g} – {high:g} ℃").format(
                low=target - tolerance, high=target + tolerance
            )
        )

    def _on_type_changed(self) -> None:
        kind = self.video_input()
        needs_path = kind is not VideoInput.CONFIG
        video_enabled = self._editable and not self._locked
        self.path_edit.setEnabled(video_enabled and needs_path)
        self.browse_button.setEnabled(video_enabled and needs_path)
        self.path_label.setVisible(needs_path)
        self.path_edit.setVisible(needs_path)
        self.browse_button.setVisible(needs_path)
        if kind is VideoInput.FILE:
            self.path_edit.setPlaceholderText(self.tr("视频文件的完整路径"))
        else:
            self.path_edit.setPlaceholderText(self.tr("图片文件夹的完整路径"))

    def _on_video_type_changed(self) -> None:
        self._on_type_changed()
        if not self._loading_settings and (
            self.video_input() is VideoInput.CONFIG or self.path_edit.text().strip()
        ):
            self._save_fields({"video_input", "video_path"})

    # ------------------------------------------------------------------ 保存

    def _save_fields(self, names: set[str]) -> bool:
        """只提交本次完成编辑的字段，避免半输入或无关草稿覆盖已保存值。"""
        if self._loading_settings or not names:
            return False
        session = self._session
        base = self._saved_draft
        if session is None or base is None or not self._editable:
            return False
        # 温度目标与偏差、视频类型与路径彼此依赖，必须作为完整有效的一组提交。
        if names & {"temperature_target_c", "temperature_tolerance_c"}:
            names = names | {"temperature_target_c", "temperature_tolerance_c"}
        if names & {"video_input", "video_path"}:
            names = names | {"video_input", "video_path"}
        self._clear_errors(names)
        entered = self.draft()
        candidate = replace(base, **{name: getattr(entered, name) for name in names})
        if candidate == base:
            return True
        try:
            result = self._services.settings.save(session, candidate)
        except SettingsError as error:
            self._show_problems(list(error.problems))
            self.save_message.setText(self.tr("设置未保存，请修正标出的内容。"))
            return False
        except (AuthError, ServiceError, StorageBusyError) as error:
            self._fail(error)
            self.save_message.setText(self._error_text(error))
            return False
        self._saved_draft = SettingsDraft.from_settings(result.settings)
        self._update_diagnostic_dates(result.settings.retention_days)
        self._apply_enabled()
        self.save_message.setText(self.tr("本项已自动保存；对之后启动的实验生效。"))
        return True

    def _show_problems(self, problems: list[SettingsProblem]) -> None:
        for problem in problems:
            text = describe_settings_problem(problem)
            if problem.name == "video_path":
                self.path_error.setText(text)
                self.path_error.setVisible(True)
            else:
                self.fields[problem.name].set_error(text)
        self.save_message.setText(
            self.tr("有 {count} 项设置不合法，没有保存。").format(count=len(problems))
        )

    def _clear_errors(self, names: set[str] | None = None) -> None:
        for name, field in self.fields.items():
            if names is None or name in names:
                field.set_error("")
        if names is None or "video_path" in names:
            self.path_error.setVisible(False)

    def _save_pending_fields(self) -> None:
        """页面离开前逐项提交已完成且有效的输入。"""
        if self._session is None or not self._editable:
            return
        base = self._saved_draft
        if base is None:
            return
        for name in _TEXT_FIELDS:
            if getattr(self.draft(), name) != getattr(base, name):
                self._save_fields({name})
                if self._saved_draft is not None:
                    base = self._saved_draft
        if self.encrypt_box.isChecked() != base.encrypt_exports:
            self._save_fields({"encrypt_exports"})
            if self._saved_draft is not None:
                base = self._saved_draft
        if (self.video_input(), self.path_edit.text()) != (base.video_input, base.video_path):
            self._save_fields({"video_input", "video_path"})
        selection = self._services.settings.runtime_selection()
        if any(edit.text() != selection[key] for key, edit in self.runtime_paths.items()):
            self.save_runtime_selection()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """离开设置页时保存已经完成编辑的有效值。"""
        self._save_pending_fields()
        self._capacity_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------ 视频预览

    def browse(self) -> None:
        """选择视频文件或图片文件夹，选中后填入路径并预览。"""
        if self.video_input() is VideoInput.FILE:
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                self.tr("选择视频文件"),
                self.path_edit.text(),
                self.tr("视频文件 (*.mp4 *.avi *.mov *.mkv *.m4v);;所有文件 (*)"),
            )
        else:
            chosen = QFileDialog.getExistingDirectory(
                self, self.tr("选择图片文件夹"), self.path_edit.text()
            )
        if chosen:
            self.path_edit.setText(chosen)
            self._save_fields({"video_input", "video_path"})
            self.preview()

    def browse_runtime_path(self, key: str) -> None:
        """选择启动配置文件或数据目录。"""
        current = self.runtime_paths[key].text()
        if key == "data_dir":
            chosen = QFileDialog.getExistingDirectory(self, self.tr("选择数据目录"), current)
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                self.tr("选择配置文件"),
                current,
                self.tr("配置文件 (*.yaml *.yml);;所有文件 (*)"),
            )
        if chosen:
            self.runtime_paths[key].setText(chosen)
            self.save_runtime_selection()

    def save_runtime_selection(self) -> None:
        """校验并自动暂存启动路径。"""
        session = self._session
        if self._loading_settings or session is None or not self._editable or self._locked:
            return
        values = {key: edit.text() for key, edit in self.runtime_paths.items()}
        if (
            values == self._services.settings.runtime_selection()
            and self._services.settings.runtime_selection_is_saved(values)
        ):
            return
        try:
            self._services.settings.save_runtime_selection(session, **values)
        except (AuthError, ServiceError, StorageBusyError) as error:
            detail = str(error) if isinstance(error, ServiceError) else self._error_text(error)
            self.save_message.setText(self.tr("启动配置无法保存：{reason}").format(reason=detail))
            return
        self.save_message.setText(
            self.tr("已自动保存启动配置；关闭并重新启动后生效。现有数据不会移动或删除。")
        )

    def preview_current(self) -> None:
        """预览当前生效的视频源（进入页面时）。"""
        self._start_preview(None, "")

    def preview(self) -> None:
        """预览输入框里的视频输入（不保存）。"""
        kind = self.video_input()
        self._start_preview(kind, self.path_edit.text())

    def _start_preview(self, kind: VideoInput | None, path: str) -> None:
        session = self._session
        if session is None:
            return
        if self._preview_token is not None:
            self._runner.cancel(self._preview_token)
        self._previewing = True
        self.preview_button.setEnabled(False)
        self.path_error.setVisible(False)
        self.probe_label.setText(self.tr("正在读取……"))
        self._preview_token = self._runner.submit(
            lambda: self._services.settings.preview_video(session, kind, path),
            self._on_probe,
            self._on_probe_failed,
        )

    def _on_probe(self, probe: VideoProbe) -> None:
        self._previewing = False
        self._preview_token = None
        self.preview_button.setEnabled(self._editable)
        fps = self.tr("{fps:g} 帧/秒").format(fps=probe.fps) if probe.fps else self.tr("帧率未知")
        count = (
            self.tr("{n} 帧").format(n=probe.frame_count) if probe.frame_count is not None else ""
        )
        parts = [f"{probe.width}×{probe.height}", fps]
        if count:
            parts.append(count)
        self.probe_label.setText(" · ".join(parts))
        if probe.frame is None:
            self.preview_view.set_image(None)
            self.preview_view.set_text(self.tr("读不到画面"))
        else:
            self.preview_view.set_image(bgr_to_qimage(probe.frame))

    def _on_probe_failed(self, error: Exception) -> None:
        self._previewing = False
        self._preview_token = None
        self.preview_button.setEnabled(self._editable)
        self.preview_view.set_image(None)
        self.preview_view.set_text(self.tr("无法预览"))
        self.probe_label.setText("")
        if isinstance(error, SettingsError):
            self.path_error.setText("；".join(describe_settings_problem(p) for p in error.problems))
        else:
            self.path_error.setText(self._error_text(error))
        self.path_error.setVisible(True)

    # ------------------------------------------------------------------ 提示

    def _error_text(self, error: Exception) -> str:
        if isinstance(error, ServiceError):
            return describe_service_error(error)
        if isinstance(error, AuthError):
            return describe_auth_error(error)
        if isinstance(error, StorageBusyError):
            return describe_storage_busy()
        return self.tr("操作失败：{reason}").format(reason=str(error))

    def _update_diagnostic_dates(self, retention_days: int | None) -> None:
        if self._session is None or not self._session.can(Permission.DIAGNOSTICS_EXPORT):
            return
        first, last = self._services.diagnostics.available_date_range(self._session)
        today = QDate(last.year, last.month, last.day)
        earliest = QDate(first.year, first.month, first.day) if first else QDate(1752, 9, 14)
        for edit in (self.diagnostics_start, self.diagnostics_end):
            edit.setSpecialValueText("")
            edit.setMinimumDate(earliest)
            edit.setMaximumDate(today)
            edit.setDate(today)
        self._diagnostic_retention = retention_days
        self._refresh_diagnostics_hint()

    def export_diagnostics(self) -> None:
        """选择目标并在后台生成诊断 ZIP。"""
        session = self._session
        if session is None or not session.can(Permission.DIAGNOSTICS_EXPORT):
            return
        start_qdate = self.diagnostics_start.date()
        end_qdate = self.diagnostics_end.date()
        start_date = (
            date(start_qdate.year(), start_qdate.month(), start_qdate.day())
            if self.diagnostics_range.isChecked()
            else None
        )
        end_date = (
            date(end_qdate.year(), end_qdate.month(), end_qdate.day())
            if self.diagnostics_range.isChecked()
            else None
        )
        if start_date is not None and end_date is not None and start_date > end_date:
            self.save_message.setText(self.tr("开始日期不能晚于结束日期。"))
            return
        suggested = f"diagnostics-{datetime.now(UTC):%Y%m%d-%H%M%S}.zip"
        chosen, _ = QFileDialog.getSaveFileName(
            self, self.tr("导出诊断包"), str(Path.home() / suggested), self.tr("ZIP 文件 (*.zip)")
        )
        if not chosen:
            return
        if Path(chosen).suffix.lower() != ".zip":
            chosen = f"{chosen}.zip"
        self._diagnostic_cancel.clear()
        dialog = QProgressDialog(self.tr("正在收集诊断资料…"), self.tr("取消"), 0, 0, self)
        dialog.setWindowTitle(self.tr("生成诊断包"))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._diagnostic_cancel.set)
        self._diagnostic_progress = dialog
        dialog.show()
        self._diagnostic_token = self._runner.submit(
            lambda: self._services.diagnostics.create_package(
                session,
                Path(chosen),
                start_date=start_date,
                end_date=end_date,
                progress=lambda done, total: self._diagnostic_relay.updated.emit(done, total),
                cancel=self._diagnostic_cancel,
            ),
            self._diagnostics_done,
            self._diagnostics_failed,
        )
        self._apply_enabled()

    def shutdown(self) -> None:
        """Cancel diagnostic exports and discard callbacks before leaving the session."""
        self._diagnostic_cancel.set()
        self._capacity_timer.stop()
        self._runner.cancel()
        self._capacity_token = None
        self._runner.wait(5000)
        self._finish_diagnostics()

    def _update_diagnostic_progress(self, done: int, total: int) -> None:
        dialog = self._diagnostic_progress
        if dialog is None:
            return
        dialog.setRange(0, total)
        dialog.setValue(done)
        dialog.setLabelText(
            self.tr("正在写入诊断文件 {done}/{total}…").format(done=done, total=total)
        )

    def _diagnostics_done(self, result: object) -> None:
        self._finish_diagnostics()
        if isinstance(result, DiagnosticPackageResult):
            note = self.tr(
                "诊断包已生成：{path}，{count} 个文件，{size} 字节。缺失/排除 {missing} 项。"
            )
            self.save_message.setText(
                note.format(
                    path=result.path,
                    count=result.file_count,
                    size=result.size_bytes,
                    missing=len(result.missing),
                )
            )

    def _diagnostics_failed(self, error: Exception) -> None:
        self._finish_diagnostics()
        if isinstance(error, DiagnosticCancelledError):
            self.save_message.setText(self.tr("诊断包生成已取消；未留下部分 ZIP 文件。"))
        else:
            self.save_message.setText(self._error_text(error))

    def _finish_diagnostics(self) -> None:
        self._diagnostic_token = None
        if self._diagnostic_progress is not None:
            self._diagnostic_progress.close()
            self._diagnostic_progress.deleteLater()
            self._diagnostic_progress = None
        self._apply_enabled()

    def _fail(self, error: Exception) -> None:
        _log.info("仪器设置操作失败：%s", error)
        Toast.show_message(self.window(), self._error_text(error), ToastKind.WARNING)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("设置"))
        self.personal_card.set_title(self.tr("个人外观"))
        self.palette_label.setText(self.tr("配色"))
        self.palette_box.setItemText(0, self.tr("石青"))
        self.palette_box.setItemText(1, self.tr("沉静蓝"))
        self.font_scale_label.setText(self.tr("字号"))
        for index, text in enumerate((self.tr("跟随系统"), self.tr("较小"), self.tr("较大"))):
            self.font_scale_box.setItemText(index, text)
        self.readonly_badge.set_text(self.tr("只读"))
        self.instrument_card.set_title(self.tr("仪器与数据"))
        self.run_card.set_title(self.tr("孵育"))
        self.temperature_card.set_title(self.tr("温度"))
        self.video_card.set_title(self.tr("视频输入"))
        self.runtime_card.set_title(self.tr("启动配置与数据目录"))
        labels = {
            "config_path": self.tr("配置文件"),
            "data_dir": self.tr("数据目录"),
            "analysis_config_path": self.tr("检测配置文件（可选）"),
        }
        for key, edit in self.runtime_paths.items():
            edit.setPlaceholderText(labels[key])
            self.runtime_browse[key].setText(self.tr("浏览…"))
        self.runtime_hint.setText(self.tr("路径选择后自动校验并保存；重启后生效。"))
        self.runtime_hint.setToolTip(
            self.tr(
                "只能选择有效的配置文件和已存在的数据目录。切换数据目录不会搬动或删除现有数据；原目录保留。实验运行中不能切换。"
            )
        )
        self.diagnostics_card.set_title(self.tr("诊断包"))
        self.diagnostics_range.setText(self.tr("指定日期范围（默认全部）"))
        self.diagnostics_start_label.setText(self.tr("开始日期"))
        self.diagnostics_end_label.setText(self.tr("结束日期"))
        self._refresh_diagnostics_hint()
        self.diagnostics_button.setText(self.tr("导出诊断包…"))

    def _request_diagnostic_capacity(self) -> None:
        if self._capacity_token is not None:
            return
        if (
            self._capacity_updated_at
            and time.monotonic() - self._capacity_updated_at < _CAPACITY_REFRESH_SECONDS
        ):
            return
        log_dir, data_dir = self._services.log_dir, self._services.data_dir
        self._capacity_updated_at = time.monotonic()
        self._capacity_token = self._runner.submit(
            lambda: _diagnostic_capacity(log_dir, data_dir),
            self._capacity_ready,
            self._capacity_failed,
        )

    def _capacity_ready(self, result: tuple[int, int]) -> None:
        self._capacity_token = None
        self._capacity = result
        self._refresh_diagnostics_hint()

    def _capacity_failed(self, _error: Exception) -> None:
        self._capacity_token = None

    def _refresh_diagnostics_hint(self) -> None:
        base = self.tr(
            "收集实验摘要、审计和可用媒体，并附校验清单。不含数据库、密码、令牌或私钥；生成过程可取消。"
        )
        self.diagnostics_hint.setToolTip(
            self.tr("诊断包还包含算法输出、复核记录和轮转日志；缺失或校验失败的媒体会列入清单。")
        )
        if self._capacity is not None:
            log_bytes, free_bytes = self._capacity
            capacity = self.tr(
                "日志约 {logs:.6f} MB（默认保留、不自动删除）；数据盘可用 {free:.1f} GB。"
            ).format(logs=log_bytes / 1_000_000, free=free_bytes / 1_000_000_000)
        else:
            capacity = self.tr("日志默认保留，不自动删除。")
        retention = getattr(self, "_diagnostic_retention", None)
        retained = (
            self.tr("诊断数据保留全部。")
            if not retention
            else self.tr("诊断数据保留最近 {days} 天。 ").format(days=retention)
        )
        self.diagnostics_hint.setText(f"{base}\n{retained} {capacity}")
        for name, field in self.fields.items():
            field.label.setText(setting_label(name))
        self.fields["retention_days"].set_hint(
            self.tr(
                "留空或 0 表示全部保留；有限天数将清理超期诊断日志及未签署已完成实验的"
                "受管视频图像，签署证据和外部原文件保留。"
            )
        )
        self.encrypt_box.setText(self.tr("敏感导出加密（Excel、批量导出报告打成带密码的 ZIP）"))
        self.encrypt_hint.setText(
            self.tr(
                "开启后每次导出都要设置密码，文件为 AES-256 加密的 ZIP。"
                "请用 7-Zip、Keka 等工具解压，系统自带的解压不支持这种加密。"
            )
        )
        self.fields["instrument_no"].set_hint(self.tr("写入之后新建的每个实验和报告。"))
        self.type_label.setText(setting_label("video_input"))
        self.path_label.setText(setting_label("video_path"))
        for index, text in enumerate(
            (self.tr("沿用配置文件"), self.tr("视频文件"), self.tr("图片文件夹"))
        ):
            self.type_box.setItemText(index, text)
        self.browse_button.setText(self.tr("浏览…"))
        self.preview_button.setText(self.tr("预览"))
        self._on_type_changed()
        self._update_range()
        if self._view is not None:
            self._apply_enabled()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
