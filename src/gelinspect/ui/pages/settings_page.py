"""仪器设置页：仪器编号、孵育和读数参数、温度、算法后端信息、视频输入、数据保留和版权方。

所有角色可以查看；修改需要 ``instrument_configure``（管理员），其他角色看到置灰的输入框和「只读」
标记。试验进行中，影响运行的项（孵育、读数、温度、视频输入）置灰，其余项仍可改。
校验和保存都在服务层：非法值逐项提示原因，一项都不会写；保存后写审计（旧值和新值），
改动保存在数据目录的用户配置文件里，不改仓库里的默认配置，对之后启动的试验生效。
视频输入只有「视频文件」和「图片文件夹」；实时摄像头尚未实现，工业相机型号确定后接入。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
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
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.imaging import bgr_to_qimage
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind
from gelinspect.ui.widgets.video_view import VideoView

_log = logging.getLogger(__name__)

_TEXT_FIELDS = (
    "instrument_no",
    "incubation_minutes",
    "frames_per_side",
    "settle_s",
    "temperature_target_c",
    "temperature_tolerance_c",
    "temperature_hold_s",
    "retention_days",
    "copyright_holder",
)


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
        self._preview_token: int | None = None
        self._previewing = False
        self._runner = BackgroundRunner(self)
        self.fields: dict[str, _Field] = {name: _Field(name) for name in _TEXT_FIELDS}

        column, _scroll = build_page_column(self)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.readonly_badge = StatusBadge("", BadgeTone.NEUTRAL)
        self.hint = Label("", "meta")
        top.addWidget(self.title)
        top.addWidget(self.readonly_badge)
        top.addWidget(self.hint)
        top.addStretch(1)
        column.addLayout(top)

        self.banner = Banner()
        column.addWidget(self.banner)

        work = QHBoxLayout()
        work.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        right = QVBoxLayout()
        right.setSpacing(14)

        self.instrument_card = Card(" ")
        for name in ("instrument_no", "copyright_holder", "retention_days"):
            self.instrument_card.body.addWidget(self.fields[name])
        left.addWidget(self.instrument_card)

        self.run_card = Card(" ")
        for name in ("incubation_minutes", "frames_per_side", "settle_s"):
            self.run_card.body.addWidget(self.fields[name])
        left.addWidget(self.run_card)

        self.temperature_card = Card(" ")
        for name in ("temperature_target_c", "temperature_tolerance_c"):
            self.temperature_card.body.addWidget(self.fields[name])
        self.range_label = Label("", "body", mono=True)
        self.temperature_card.body.addWidget(self.range_label)
        self.temperature_card.body.addWidget(self.fields["temperature_hold_s"])
        left.addWidget(self.temperature_card)
        left.addStretch(1)

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
        self.source_label = Label("", "meta")
        self.source_label.setWordWrap(True)
        self.camera_note = Label("", "meta")
        self.camera_note.setWordWrap(True)
        for widget in (self.source_label, self.camera_note):
            self.video_card.body.addWidget(widget)
        right.addWidget(self.video_card)

        self.backend_card = Card(" ")
        self.backend_labels: dict[str, tuple[Label, Label]] = {}
        for key in ("backend", "model", "config", "config_file"):
            row = QVBoxLayout()
            row.setSpacing(2)
            name_label, value_label = Label("", "lab"), Label("", "body", mono=True)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(name_label)
            row.addWidget(value_label)
            self.backend_card.body.addLayout(row)
            self.backend_labels[key] = (name_label, value_label)
        right.addWidget(self.backend_card)
        right.addStretch(1)

        work.addLayout(left, 1)
        work.addLayout(right, 1)
        column.addLayout(work)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.save_message = Label("", "meta")
        self.save_message.setWordWrap(True)
        self.revert_button = Button("")
        self.save_button = Button("", ButtonVariant.PRIMARY)
        bar.addWidget(self.save_message, 1)
        bar.addWidget(self.revert_button)
        bar.addWidget(self.save_button)
        column.addLayout(bar)
        column.addStretch(1)

        self.type_box.currentIndexChanged.connect(self._on_type_changed)
        self.browse_button.clicked.connect(self.browse)
        self.preview_button.clicked.connect(self.preview)
        self.save_button.clicked.connect(self.save)
        self.revert_button.clicked.connect(self.refresh)
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
        session = self._session
        if session is None:
            return
        try:
            self._view = self._services.settings.current(session)
        except (AuthError, StorageBusyError) as error:
            self._fail(error)
            return
        view = self._view
        self._editable = session.can(Permission.INSTRUMENT_CONFIGURE)
        self._locked = view.running_experiment is not None
        self._fill(SettingsDraft.from_settings(view.settings))
        self._clear_errors()
        self.save_message.setText("")
        self._render_backend(view)
        self._apply_enabled()
        self.preview_current()

    def draft(self) -> SettingsDraft:
        """按输入框当前内容生成草稿（保存前由服务层校验）。

        Returns:
            草稿。
        """
        text = {name: field.edit.text() for name, field in self.fields.items()}
        return SettingsDraft(
            instrument_no=text["instrument_no"],
            incubation_minutes=text["incubation_minutes"],
            frames_per_side=text["frames_per_side"],
            settle_s=text["settle_s"],
            temperature_target_c=text["temperature_target_c"],
            temperature_tolerance_c=text["temperature_tolerance_c"],
            temperature_hold_s=text["temperature_hold_s"],
            video_input=self.video_input(),
            video_path=self.path_edit.text(),
            retention_days=text["retention_days"],
            copyright_holder=text["copyright_holder"],
        )

    def video_input(self) -> VideoInput:
        """选中的视频输入类型。

        Returns:
            类型。
        """
        return VideoInput(self.type_box.currentData())

    def set_video_input(self, kind: VideoInput, path: str = "") -> None:
        """设置视频输入类型和路径（不保存）。

        Args:
            kind: 类型。
            path: 路径。
        """
        self.type_box.setCurrentIndex(self.type_box.findData(kind.value))
        self.path_edit.setText(path)

    def _fill(self, draft: SettingsDraft) -> None:
        for name, field in self.fields.items():
            field.edit.setText(getattr(draft, name))
        self.type_box.blockSignals(True)
        self.type_box.setCurrentIndex(self.type_box.findData(draft.video_input.value))
        self.type_box.blockSignals(False)
        self.path_edit.setText(draft.video_path)
        self._on_type_changed()
        self._update_range()

    def _render_backend(self, view: SettingsView) -> None:
        info = view.backend
        pending = self.tr("第一次试验后显示")
        values = {
            "backend": info.analysis_backend,
            "model": info.model_version or pending,
            "config": info.config_version or pending,
            "config_file": info.analysis_config_path or "–",
        }
        for key, (_name, value) in self.backend_labels.items():
            value.setText(values[key])
        self.source_label.setText(
            self.tr("配置文件里的视频源：{source}").format(source=info.video_source)
        )

    def _apply_enabled(self) -> None:
        editable = self._editable
        for name, field in self.fields.items():
            impacting = name in IMPACTING_FIELDS
            field.edit.setEnabled(editable and not (impacting and self._locked))
        video_enabled = editable and not self._locked
        self.type_box.setEnabled(video_enabled)
        self.path_edit.setEnabled(video_enabled and self.video_input() is not VideoInput.CONFIG)
        self.browse_button.setEnabled(video_enabled and self.video_input() is not VideoInput.CONFIG)
        self.preview_button.setEnabled(editable and not self._previewing)
        self.save_button.setEnabled(editable)
        self.revert_button.setEnabled(editable)
        self.readonly_badge.setVisible(not editable)
        view = self._view
        if not editable:
            self.banner.show_message(
                "info",
                "info",
                self.tr("只读"),
                self.tr("当前角色只能查看仪器设置，修改需要管理员权限。"),
            )
        elif self._locked and view is not None:
            self.banner.show_message(
                "warning",
                "triangle-alert",
                self.tr("试验 {id} 正在运行").format(id=view.running_experiment),
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

    # ------------------------------------------------------------------ 保存

    def save(self) -> bool:
        """校验并保存；有不合法的项时逐项提示，什么都不保存。

        Returns:
            已保存（或没有改动）时为 True。
        """
        session = self._session
        if session is None:
            return False
        self._clear_errors()
        try:
            result = self._services.settings.save(session, self.draft())
        except SettingsError as error:
            self._show_problems(list(error.problems))
            return False
        except (AuthError, ServiceError, StorageBusyError) as error:
            self._fail(error)
            self.save_message.setText(self._error_text(error))
            return False
        self.refresh()
        if result.changed:
            message = self.tr("设置已保存，对之后启动的试验生效。")
            Toast.show_message(self.window(), message, ToastKind.SUCCESS)
        else:
            message = self.tr("没有改动。")
        self.save_message.setText(message)
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

    def _clear_errors(self) -> None:
        for field in self.fields.values():
            field.set_error("")
        self.path_error.setVisible(False)

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
            self.preview()

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

    def _fail(self, error: Exception) -> None:
        _log.info("仪器设置操作失败：%s", error)
        Toast.show_message(self.window(), self._error_text(error), ToastKind.WARNING)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("仪器设置"))
        self.readonly_badge.set_text(self.tr("只读"))
        self.hint.setText(self.tr("改动保存在数据目录的用户配置里，对之后启动的试验生效"))
        self.instrument_card.set_title(self.tr("仪器与数据"))
        self.run_card.set_title(self.tr("孵育与读数"))
        self.temperature_card.set_title(self.tr("温度"))
        self.video_card.set_title(self.tr("视频输入"))
        self.backend_card.set_title(self.tr("算法后端"))
        for name, field in self.fields.items():
            field.label.setText(setting_label(name))
        self.fields["retention_days"].set_hint(
            self.tr("留空表示不自动删除（默认）。到期清理尚未启用，目前不会删除任何文件。")
        )
        self.fields["instrument_no"].set_hint(self.tr("写入之后新建的每个试验和报告。"))
        self.type_label.setText(setting_label("video_input"))
        self.path_label.setText(setting_label("video_path"))
        for index, text in enumerate(
            (self.tr("沿用配置文件"), self.tr("视频文件"), self.tr("图片文件夹"))
        ):
            self.type_box.setItemText(index, text)
        self.browse_button.setText(self.tr("浏览…"))
        self.preview_button.setText(self.tr("预览"))
        self.camera_note.setText(self.tr("工业相机：型号确定后接入。"))
        names = {
            "backend": self.tr("后端"),
            "model": self.tr("模型版本"),
            "config": self.tr("配置版本"),
            "config_file": self.tr("配置文件"),
        }
        for key, (name_label, _value) in self.backend_labels.items():
            name_label.setText(names[key])
        self.revert_button.setText(self.tr("还原"))
        self.save_button.setText(self.tr("保存设置"))
        self._on_type_changed()
        self._update_range()
        if self._view is not None:
            self._render_backend(self._view)
            self._apply_enabled()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
