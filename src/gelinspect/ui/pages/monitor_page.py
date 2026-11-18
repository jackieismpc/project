"""试验监控页：数据来自引擎和视频管线。

* 进行中的试验：孔板、倒计时、结果表随引擎事件刷新，实时视频卡显示管线预览帧。
* 已结束的试验（历史回看）：只读，没有停止按钮和实时视频，有回放和结果。

事件很密（每秒十几条倒计时、每管一条孔更新），所以事件只改内存里的状态并请求一次刷新，
刷新由单次定时器合并，孔板和表格最多每 100 ms 重绘一次。预览帧由桥保证只保留最新一帧。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import ReagentKind
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import ROW_COUNT, row_label
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.engine_types import (
    AlertCode,
    AlertLevel,
    EngineAlert,
    RowCaptured,
    RowCountdown,
    RowPhase,
    StateChanged,
    TemperatureChanged,
    WellStatus,
    WellUpdated,
)
from gelinspect.services.errors import ServiceError
from gelinspect.services.monitor_service import ExperimentView
from gelinspect.services.video.events import VideoEvent, VideoEventCode, VideoEventLevel
from gelinspect.ui.bridge import EngineBridge
from gelinspect.ui.labels import (
    builtin_name_text,
    describe_alert,
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    describe_video_event,
    reagent_kind_label,
)
from gelinspect.ui.pages.monitor_format import (
    countdown,
    experiment_title,
    mono_span,
    status_badge,
    well_state,
)
from gelinspect.ui.pages.monitor_images import ImagesTab
from gelinspect.ui.pages.monitor_report import MonitorReportPreview
from gelinspect.ui.pages.replay_dialog import ReplayDialog
from gelinspect.ui.pages.template_editor import TemplateEditorDialog
from gelinspect.ui.punctuation import join_items
from gelinspect.ui.widgets.alert_stack import AlertStack
from gelinspect.ui.widgets.badge import StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateLegend, plate_title
from gelinspect.ui.widgets.replay_player import ReplayPlayer
from gelinspect.ui.widgets.tabs import Tabs
from gelinspect.ui.widgets.toast import Toast, ToastKind
from gelinspect.ui.widgets.video_view import VideoView

REFRESH_INTERVAL_MS = 100
"""孔板和表格的最短刷新间隔（≤10 Hz）。"""
TAB_DATA, TAB_IMAGES, TAB_REPORT = range(3)

_VIDEO_TONES = {
    VideoEventLevel.INFO: "info",
    VideoEventLevel.WARNING: "warning",
    VideoEventLevel.ERROR: "danger",
}
_ENGINE_TONES = {AlertLevel.INFO: "info", AlertLevel.WARNING: "warning", AlertLevel.ERROR: "danger"}
_RESOLVING = {
    AlertCode.TEMPERATURE_RECOVERED: AlertCode.TEMPERATURE_EXCURSION,
    AlertCode.VIDEO_RECONNECTED: AlertCode.VIDEO_DISCONNECTED,
}
_VIDEO_RESOLVING = {VideoEventCode.RECONNECTED: VideoEventCode.DISCONNECTED}
_VIDEO_HIDDEN = frozenset({VideoEventCode.LOW_FPS, VideoEventCode.FRAMES_DROPPED})
"""不打扰操作员的视频事件（已有统计，不弹横幅）。"""


def _clock(value: datetime | None) -> str:
    return "–" if value is None else value.astimezone().strftime("%H:%M:%S")


def _plain(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


class MonitorPage(QWidget):
    """试验监控页。

    Signals:
        tubeRequested: 请求打开单管详情，参数为试验标识和管号。
        navigationRequested: 请求跳转到某页面，参数为路由标识。
    """

    tubeRequested = Signal(str, int)  # noqa: N815
    navigationRequested = Signal(str)  # noqa: N815
    reportRequested = Signal(str)  # noqa: N815

    def __init__(
        self, services: AppServices, bridge: EngineBridge, parent: QWidget | None = None
    ) -> None:
        """创建页面并连接桥的信号。

        Args:
            services: 界面服务。
            bridge: 引擎事件桥。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._bridge = bridge
        self._session: Session | None = None
        self._view: ExperimentView | None = None
        self._status: dict[int, WellStatus | None] = {}
        self._rows: dict[int, tuple[RowPhase | None, float | None]] = {}
        self._recordings: list[int] = []
        self._selected_video_row: int | None = None
        self._tube_focus: int | None = None
        self._reload_pending = False
        self._dirty = False
        self._closed = False
        self._signature: object = None
        self._replay_dialog: ReplayDialog | None = None
        self._angle: float | None = None
        self._fps_mark = (time.monotonic(), 0)
        self._fps = 0.0
        self.refresh_count = 0
        """孔板和表格实际重绘的次数（测试用，验证合并刷新）。"""

        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        self.scroll_area = scroll
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        self._column = QVBoxLayout(page)
        self._column.setContentsMargins(24, 20, 24, 28)
        self._column.setSpacing(14)

        self._build_empty()
        self._build_header()
        self.alerts = AlertStack()
        self._column.addWidget(self.alerts)
        self._build_facts()
        self.tabs = Tabs(["", "", ""])
        self._column.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self._column.addWidget(self.stack)
        self._build_data_tab()
        self.images_tab = ImagesTab(services.media)
        self.stack.addWidget(self.images_tab)
        self.report_tab = self._build_report_tab()
        self.stack.addWidget(self.report_tab)
        self._column.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._flush)

        self.tabs.currentChanged.connect(self._on_tab)
        self.plate.tubeClicked.connect(self._on_tube_clicked)
        self.plate.rowActivated.connect(self._on_row_title_activated)
        self.table.rowActivated.connect(self._on_row_activated)
        self.stop_button.clicked.connect(self.request_stop)
        self.resume_button.clicked.connect(self.request_resume)
        self.retry_button.clicked.connect(self.request_retry)
        self.replay_button.clicked.connect(self.open_replay)
        self.replay_rows.currentIndexChanged.connect(self._on_replay_row)
        bridge.wellUpdated.connect(self._on_well)
        bridge.rowCountdown.connect(self._on_countdown)
        bridge.stateChanged.connect(self._on_state)
        bridge.temperatureChanged.connect(self._on_temperature)
        bridge.alertRaised.connect(self._on_alert)
        bridge.videoEvent.connect(self._on_video_event)
        bridge.previewFrame.connect(self._on_preview)
        bridge.rowCaptured.connect(self._on_captured)

        self.retranslate()
        self._show_empty(True)

    # ------------------------------------------------------------------ 搭建

    def _build_empty(self) -> None:
        self.empty_card = Card()
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("video", 36, "ink_muted"))
        self.empty_title = Label("", "val")
        self.empty_hint = Label("", "body")
        self.empty_hint.setWordWrap(True)
        self.empty_history = Button("", ButtonVariant.PRIMARY)
        self.empty_history.clicked.connect(lambda: self.navigationRequested.emit("history"))
        for widget in (icon, self.empty_title, self.empty_hint):
            self.empty_card.body.addWidget(widget)
        self.empty_card.body.addWidget(self.empty_history, 0, Qt.AlignmentFlag.AlignLeft)
        self._column.addWidget(self.empty_card)

    def _build_header(self) -> None:
        self.header = QWidget()
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.title = Label("", "h1")
        self.id_label = Label("", "meta", mono=True)
        self.badge = StatusBadge("")
        self.temperature = Label("", "readout", mono=True)
        self.resume_button = Button("", ButtonVariant.PRIMARY, icon="play")
        self.retry_button = Button("", ButtonVariant.SECONDARY, icon="rotate-ccw")
        self.stop_button = Button("", ButtonVariant.DANGER, icon="square")
        for widget in (self.title, self.id_label, self.badge):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.temperature)
        row.addWidget(self.resume_button)
        row.addWidget(self.retry_button)
        row.addWidget(self.stop_button)
        self._column.addWidget(self.header)
        self.recovery_hint = Label("", "warn")
        self.recovery_hint.setWordWrap(True)
        self.recovery_hint.setVisible(False)
        self._column.addWidget(self.recovery_hint)

    def _build_facts(self) -> None:
        self.facts_card = Card()
        row = QHBoxLayout()
        row.setSpacing(20)
        self._fact_labels: dict[str, Label] = {}
        self._fact_values: dict[str, Label] = {}
        for key, mono in (
            ("lal", True),
            ("endotoxin", True),
            ("water", True),
            ("lambda", True),
            ("operator", False),
            ("started", True),
        ):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            label = Label("", "lab")
            value = Label("", "val", mono=mono)
            cell.addWidget(label)
            cell.addWidget(value)
            row.addLayout(cell, 1)
            self._fact_labels[key] = label
            self._fact_values[key] = value
        self.facts_card.body.addLayout(row)
        self._column.addWidget(self.facts_card)

    def _build_data_tab(self) -> None:
        data = QWidget()
        layout = QVBoxLayout(data)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        work = QHBoxLayout()
        work.setSpacing(14)
        self.plate_card = Card(" ")
        assert self.plate_card.header is not None
        self.plate_card.header.addWidget(PlateLegend())
        self.plate = PlateGrid()
        self.plate_card.body.addWidget(self.plate)
        work.addWidget(self.plate_card, 2)

        self.video_card = Card(" ")
        assert self.video_card.header is not None
        self.video_state = Label("", "meta")
        self.video_card.header.addWidget(self.video_state)
        dark = Card(role="video")
        dark.body.setContentsMargins(10, 10, 10, 8)
        dark.body.setSpacing(6)
        self.video = VideoView(min_height=200)
        dark.body.addWidget(self.video)
        captions = QHBoxLayout()
        self.video_left = Label("", "video-meta", mono=True)
        self.video_right = Label("", "video-meta", mono=True)
        captions.addWidget(self.video_left)
        captions.addStretch(1)
        captions.addWidget(self.video_right)
        dark.body.addLayout(captions)
        self.video_card.body.addWidget(dark)
        self.algo_info = Label("", "meta")
        self.algo_info.setTextFormat(Qt.TextFormat.RichText)
        self.video_card.body.addWidget(self.algo_info)
        self.replay_button = Button("")
        self.video_card.body.addWidget(self.replay_button)
        self.video_card.body.addStretch(1)
        self.live_box = dark

        self.replay_card = Card(" ")
        self.replay_rows = QComboBox()
        assert self.replay_card.header is not None
        self.replay_card.header.addWidget(self.replay_rows)
        self.replay_player = ReplayPlayer()
        self.replay_card.body.addWidget(self.replay_player)
        self.replay_empty = Label("", "meta")
        self.replay_empty.setWordWrap(True)
        self.replay_card.body.addWidget(self.replay_empty)
        work.addWidget(self.video_card, 1)
        work.addWidget(self.replay_card, 1)
        layout.addLayout(work)

        self.table_card = Card(" ")
        self.table = DataTable(self._columns(), fit_content=True)
        self.table_card.body.addWidget(self.table)
        layout.addWidget(self.table_card)
        self.stack.addWidget(data)

    def _build_report_tab(self) -> Card:
        """“报告打印预览”标签：真实分页预览，并可到报告中心处理。"""
        card = Card()
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("file-text", 36, "ink_muted"))
        self.report_headline = Label("", "val")
        self.report_summary = Label("", "body")
        self.report_summary.setWordWrap(True)
        self.report_open = Button("", ButtonVariant.PRIMARY, icon="printer")
        self.report_open.clicked.connect(self._open_report)
        self.save_template_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        self.save_template_button.clicked.connect(self.save_as_template)
        self.save_template_button.hide()
        for widget in (icon, self.report_headline, self.report_summary):
            card.body.addWidget(widget)
        card.body.addWidget(self.report_open, 0, Qt.AlignmentFlag.AlignLeft)
        card.body.addWidget(self.save_template_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.report_preview = MonitorReportPreview(self._services.reports)
        card.body.addWidget(self.report_preview)
        return card

    def _open_report(self) -> None:
        view = self._view
        if view is not None:
            self.reportRequested.emit(view.experiment_id)

    def save_as_template(self) -> None:
        """从结束试验的保存快照另存为新模板。"""
        session = self._session
        view = self._view
        if (
            session is None
            or view is None
            or view.live
            or view.state
            not in {
                ExperimentState.PENDING_REVIEW,
                ExperimentState.COMPLETED,
                ExperimentState.SIGNED,
            }
            or not session.can(Permission.TEMPLATE_MANAGE)
            or not session.can(Permission.EXPERIMENT_VIEW)
        ):
            return
        try:
            draft = self._services.templates.template_draft_from_experiment(
                session, view.experiment_id
            )
        except AuthError as error:
            Toast.show_message(self.window(), describe_auth_error(error), ToastKind.WARNING)
            return
        except ServiceError as error:
            Toast.show_message(self.window(), describe_service_error(error), ToastKind.WARNING)
            return
        except StorageBusyError:
            Toast.show_message(self.window(), describe_storage_busy(), ToastKind.WARNING)
            return
        dialog = TemplateEditorDialog(
            lambda draft: self._services.templates.create_template(session, draft),
            initial=draft,
            parent=self,
        )
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            saved = dialog.saved
        finally:
            dialog.deleteLater()
        if accepted and saved is not None:
            Toast.show_message(
                self.window(),
                self.tr("已另存模板：{name}").format(name=saved.name),
                ToastKind.SUCCESS,
            )

    def _render_report_tabs(self) -> None:
        """按当前试验刷新报告预览标签。"""
        view = self._view
        self.report_open.setEnabled(view is not None)
        self.report_preview.set_context(
            self._session,
            view.experiment_id if view is not None else None,
            (view.state, view.tubes) if view is not None else None,
        )
        if view is None:
            self.report_summary.setText(self.tr("先选择一个实验。"))
            return
        if view.state is ExperimentState.SIGNED:
            text = self.tr("这个实验已签署，可以预览、打印、验签和导出已签名的 PDF。")
        elif view.state is ExperimentState.COMPLETED:
            text = self.tr("这个实验已完成，可以预览报告；有签署权限的用户核对后签署。")
        else:
            text = self.tr("这个实验还没有完成，可以先预览报告；完成并复核后才能签署。")
        self.report_summary.setText(text)

    def _columns(self) -> list[Column]:
        return [
            Column("tube", self.tr("试管编号"), mono=True),
            Column("sample", self.tr("样品名称")),
            Column("serial", self.tr("序列号"), mono=True),
            Column("batch", self.tr("批号"), mono=True),
            Column("dilution", self.tr("稀释倍数"), mono=True),
            Column("spike", self.tr("外加内毒素"), mono=True),
            Column("result", self.tr("阴阳性"), kind="state"),
        ]

    # ------------------------------------------------------------------ 打开

    def set_session(self, session: Session | None) -> None:
        """设置当前会话。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        self.report_preview.set_context(session, None, None)
        self._render_permissions()

    @property
    def experiment_id(self) -> str | None:
        """正在显示的试验。"""
        return self._view.experiment_id if self._view is not None else None

    @property
    def view_model(self) -> ExperimentView | None:
        """最近一次读到的试验视图。"""
        return self._view

    @property
    def read_only(self) -> bool:
        """是否为只读回看（试验不在运行）。"""
        return self._view is None or not self._view.live

    def open_experiment(self, experiment_id: str | None) -> bool:
        """显示某个试验；None 表示默认的（运行中的，否则最近的）。

        Args:
            experiment_id: 试验标识。

        Returns:
            成功显示时为 True；没有试验或没有权限时显示空状态并返回 False。
        """
        self.replay_player.unload()
        self._selected_video_row = None
        self._tube_focus = None
        self.plate.set_active_row(None)
        try:
            target = experiment_id or self._services.monitor.default_experiment_id(self._session)
            view = (
                None
                if target is None
                else self._services.monitor.view(
                    self._session, target, audit_access=experiment_id is not None
                )
            )
        except (AuthError, ServiceError):
            view = None
        self.alerts.clear()
        self._angle = None
        self._signature = None
        self._view = view
        if view is None:
            self.report_preview.set_context(self._session, None, None)
            self._show_empty(True)
            self.images_tab.set_experiment(self._session, None)
            self._render_permissions()
            return False
        self._show_empty(False)
        self._adopt(view)
        self.images_tab.set_experiment(self._session, view.experiment_id)
        if view.live:
            for event in self._services.monitor.recent_video_alerts():
                self._on_video_event(event)
        self.tabs.set_current(TAB_DATA)
        self.stack.setCurrentIndex(TAB_DATA)
        return True

    def reload(self) -> None:
        """重新读取当前试验（复核后、状态变化后）。"""
        if self._view is None:
            return
        try:
            view = self._services.monitor.view(self._session, self._view.experiment_id)
        except (AuthError, ServiceError):
            return
        self._view = view
        self._adopt(view)
        if self.tabs.current() == TAB_IMAGES:
            self.images_tab.refresh()

    def _adopt(self, view: ExperimentView) -> None:
        self._status = {t.tube_no: t.status for t in view.tubes}
        self._rows = {r.row_index: (r.phase, r.remaining_s) for r in view.rows}
        try:
            self._recordings = sorted(
                self._services.media.recordings(self._session, view.experiment_id)
            )
        except AuthError:
            self._recordings = []
        self._render_header()
        self._render_facts()
        self._render_video()
        self._signature = None
        self._flush()

    def _show_empty(self, empty: bool) -> None:
        self.empty_card.setVisible(empty)
        for widget in (self.header, self.facts_card, self.tabs, self.stack):
            widget.setVisible(not empty)
        if empty:
            self.recovery_hint.setVisible(False)
        self.alerts.setVisible(not empty and bool(self.alerts.active_keys()))

    # ------------------------------------------------------------------ 事件

    def _mine(self, experiment_id: str) -> bool:
        return (
            not self._closed
            and self._view is not None
            and experiment_id == self._view.experiment_id
        )

    def _request_flush(self) -> None:
        self._dirty = True
        if not self._timer.isActive():
            self._timer.start()

    def _on_well(self, event: WellUpdated) -> None:
        if self._mine(event.experiment_id):
            self._status[event.tube_no] = event.status
            self._request_flush()

    def _on_countdown(self, event: RowCountdown) -> None:
        if self._mine(event.experiment_id):
            self._rows[event.row_index] = (event.phase, event.remaining_s)
            self._request_flush()

    def _on_state(self, event: StateChanged) -> None:
        if self._mine(event.experiment_id):
            self._reload_pending = True
            self._request_flush()

    def _on_temperature(self, event: TemperatureChanged) -> None:
        if self._mine(event.experiment_id) and self._view is not None and self._view.live:
            self.temperature.setText("–– ℃" if event.celsius is None else f"{event.celsius:.1f} ℃")

    def _on_captured(self, event: RowCaptured) -> None:
        if not self._mine(event.experiment_id):
            return
        self._angle = event.flip_angle_deg
        self.images_tab.mark_stale(event.row_index)
        if self.tabs.current() == TAB_IMAGES:
            self.images_tab.refresh()
        self._reload_pending = True
        self._request_flush()

    def _on_alert(self, alert: EngineAlert) -> None:
        if not self._mine(alert.experiment_id):
            return
        resolved = _RESOLVING.get(alert.code)
        if resolved is not None:
            self.alerts.clear_key(f"engine:{resolved.value}")
            return
        if alert.code in (AlertCode.STOP_IGNORED,):
            return
        text = describe_alert(
            alert.code,
            alert.message,
            alert.row_index,
            closed=alert.level is AlertLevel.INFO,
        )
        self.alerts.post(f"engine:{alert.code.value}", _ENGINE_TONES[alert.level], text)
        self.alerts.setVisible(True)

    def _on_video_event(self, event: VideoEvent) -> None:
        if self._closed or self._view is None or not self._view.live or event.code in _VIDEO_HIDDEN:
            return
        if event.active and event.code in {
            VideoEventCode.STREAM_STALLED,
            VideoEventCode.DISCONNECTED,
            VideoEventCode.RECONNECTING,
            VideoEventCode.RECONNECT_FAILED,
        }:
            self.video.set_image(None)
            self.video.set_text(describe_video_event(event.code, event.message))
        key = f"video:{event.code.value}"
        resolved = _VIDEO_RESOLVING.get(event.code)
        if resolved is not None:
            self.alerts.clear_key(f"video:{resolved.value}")
            return
        if not event.active:
            self.alerts.clear_key(key)
            return
        if event.level is VideoEventLevel.INFO:
            return
        self.alerts.post(
            key, _VIDEO_TONES[event.level], describe_video_event(event.code, event.message)
        )
        self.alerts.setVisible(True)

    def _on_preview(self, image: QImage) -> None:
        if self._closed or self._view is None or not self._view.live:
            return
        self.video.set_image(image)
        now = time.monotonic()
        mark_time, mark_frames = self._fps_mark
        if now - mark_time >= 1.0:
            shown = self.video.frames_shown
            self._fps = (shown - mark_frames) / (now - mark_time)
            self._fps_mark = (now, shown)
            self._render_video_meta()

    # ------------------------------------------------------------------ 刷新

    def _flush(self) -> None:
        """合并后的刷新：必要时先重读视图，再重绘孔板和表格。"""
        self._timer.stop()
        if self._reload_pending:
            self._reload_pending = False
            self.reload_quiet()
        if not self._dirty and self._signature is not None:
            return
        self._dirty = False
        view = self._view
        if view is None:
            return
        self.refresh_count += 1
        states: dict[int, WellState] = {
            t.tube_no: well_state(self._status.get(t.tube_no)) for t in view.tubes
        }
        self.plate.set_states(states)
        for index in range(1, ROW_COUNT + 1):
            if index not in self._rows:
                self.plate.set_countdown(index, "")
                continue
            phase, remaining = self._rows[index]
            done = all((t.result is not None) for t in view.tubes if t.row_index == index)
            text, active = countdown(phase, remaining, done=done)
            self.plate.set_countdown(index, text, active=active)
        rows = [
            {
                "tube": t.tube_no,
                "sample": builtin_name_text(t.sample_name),
                "serial": t.serial_no or "",
                "batch": t.batch_no or "",
                "dilution": _plain(t.dilution_factor),
                "spike": _plain(t.spiked_endotoxin),
                "result": states[t.tube_no],
            }
            for t in view.tubes
        ]
        signature = tuple((r["tube"], r["result"]) for r in rows)
        if signature != self._signature:
            self._signature = signature
            self.table.set_rows(rows)
        self._render_header()
        self._render_video_meta()

    def reload_quiet(self) -> None:
        """重读视图但不触发重绘（供 :meth:`_flush` 内部使用）。"""
        if self._view is None:
            return
        try:
            view = self._services.monitor.view(self._session, self._view.experiment_id)
        except (AuthError, ServiceError):
            return
        self._view = view
        self._status = {t.tube_no: t.status for t in view.tubes}
        self._rows = {r.row_index: (r.phase, r.remaining_s) for r in view.rows}
        try:
            self._recordings = sorted(
                self._services.media.recordings(self._session, view.experiment_id)
            )
        except AuthError:
            self._recordings = []
        self._dirty = True
        self._render_facts()
        self._render_video()

    # ------------------------------------------------------------------ 显示

    def _render_header(self) -> None:
        self._render_report_tabs()
        view = self._view
        if view is None:
            return
        self.title.setText(experiment_title(view))
        self.id_label.setText(self.tr("实验 {id}").format(id=view.experiment_id))
        text, tone = status_badge(view)
        self.badge.set_text(text)
        self.badge.set_tone(tone)
        self.temperature.setVisible(view.live)
        if view.live and view.temperature_c is not None and "℃" not in self.temperature.text():
            self.temperature.setText(f"{view.temperature_c:.1f} ℃")
        elif view.live and not self.temperature.text():
            self.temperature.setText("–– ℃")
        self.stop_button.setVisible(view.can_stop)
        self.resume_button.setVisible(view.can_resume)
        self.retry_button.setVisible(bool(view.retry_rows or view.failed_rows))
        self._render_recovery_hint()
        self._render_permissions()

    def _render_permissions(self) -> None:
        allowed = self._session is not None and self._session.can(Permission.EXPERIMENT_STOP)
        self.stop_button.setEnabled(allowed)
        self.stop_button.setToolTip("" if allowed else self.tr("当前角色无此权限"))
        view = self._view
        runnable = self._session is not None and self._session.can(Permission.EXPERIMENT_RUN)
        denied = "" if runnable else self.tr("当前角色无此权限")
        self.resume_button.setEnabled(runnable)
        self.resume_button.setToolTip(denied)
        waiting = view is not None and bool(view.failed_rows) and not view.retry_rows
        self.retry_button.setEnabled(runnable and not waiting)
        self.retry_button.setToolTip(
            denied or (self.tr("全部排读完后才能重试分析") if waiting else "")
        )
        can_save_template = (
            self._session is not None
            and self._session.can(Permission.TEMPLATE_MANAGE)
            and self._session.can(Permission.EXPERIMENT_VIEW)
            and view is not None
            and not view.live
            and view.state
            in {
                ExperimentState.PENDING_REVIEW,
                ExperimentState.COMPLETED,
                ExperimentState.SIGNED,
            }
        )
        self.save_template_button.setVisible(can_save_template)
        self.save_template_button.setEnabled(can_save_template)

    def _render_recovery_hint(self) -> None:
        view = self._view
        if view is None:
            return
        hint = ""
        if view.interrupted and view.retry_rows and view.unread_rows:
            hint = self.tr(
                "软件重启过，调度已经丢失，这个实验不能恢复。"
                "第 {rows} 排已抓帧，可以重试分析（不翻转）；"
                "第 {unread} 排错过了读数时间，所以分析后实验仍只能停止。"
            ).format(
                rows=join_items(view.retry_rows),
                unread=join_items(view.unread_rows),
            )
        elif view.interrupted and view.retry_rows:
            hint = self.tr(
                "软件重启过，调度已经丢失，这个实验不能恢复。"
                "全部排都已抓帧：第 {rows} 排可以重试分析（不翻转），"
                "完成后实验照常结束。"
            ).format(rows=join_items(view.retry_rows))
        elif view.interrupted:
            hint = self.tr(
                "软件重启过，调度已经丢失，这个实验不能恢复。没有可以重试分析的排（没有抓帧，或抓帧记录缺少抓帧时间），只能停止实验。"
            )
        elif view.failed_rows and not view.retry_rows:
            hint = self.tr("第 {rows} 排分析失败，原图已保留。全部排读完后可以重试分析。").format(
                rows=join_items(view.failed_rows)
            )
        elif view.can_resume:
            hint = self.tr("实验异常：{reason}").format(reason=view.fault_reason or "–")
        self.recovery_hint.setText(hint)
        self.recovery_hint.setVisible(bool(hint))

    def _render_facts(self) -> None:
        view = self._view
        if view is None:
            return
        batches = view.reagent_batches
        values = {
            "lal": batches.get(ReagentKind.LAL_REAGENT, "–"),
            "endotoxin": batches.get(ReagentKind.ENDOTOXIN, "–"),
            "water": batches.get(ReagentKind.WATER, "–"),
            "lambda": "–"
            if view.labeled_sensitivity is None
            else f"{view.labeled_sensitivity:g} EU/ml",
            "operator": view.operator_name,
            "started": _clock(view.started_at),
        }
        for key, value in values.items():
            self._fact_values[key].setText(value)

    def _render_video(self) -> None:
        view = self._view
        if view is None:
            return
        live = view.live
        row_playback = self._selected_video_row is not None
        self.video_card.setVisible(live and not row_playback)
        self.replay_card.setVisible(not live or row_playback)
        self.replay_button.setEnabled(bool(self._recordings))
        if self._recordings:
            self.replay_button.setText(self.tr("回放第 {n} 排").format(n=self._recordings[-1]))
        else:
            self.replay_button.setText(self.tr("回放（还没有录像）"))
        if not live or row_playback:
            current = self._selected_video_row or self.replay_rows.currentData()
            self.replay_rows.blockSignals(True)
            self.replay_rows.clear()
            for row in self._recordings:
                self.replay_rows.addItem(row_label(row), row)
            index = self.replay_rows.findData(current)
            self.replay_rows.setCurrentIndex(index if row_playback else max(0, index))
            self.replay_rows.blockSignals(False)
            self.replay_rows.setVisible(bool(self._recordings))
            missing = row_playback and current not in self._recordings
            self.replay_empty.setVisible(missing or not self._recordings)
            self.replay_player.setVisible(row_playback or bool(self._recordings))
            if missing:
                self.replay_empty.setText(self.tr("这个排没有录像"))
                self.replay_player.show_message(self.tr("这个排没有录像"))
            elif self._recordings and self.replay_player.clip is None:
                self._load_inline(self.replay_rows.currentData(), autoplay=False)
        else:
            if self.video.image() is None:
                self.video.set_text(self.tr("等待视频画面…"))
        self._render_video_meta()

    def _render_video_meta(self) -> None:
        view = self._view
        if view is None or not view.live:
            return
        active = next(
            (r for r in view.rows if r.phase in (RowPhase.READING, RowPhase.ANALYZING)), None
        )
        if active is not None and active.phase is RowPhase.READING:
            state, left = (
                self.tr("翻转录制中"),
                f"{row_label(active.row_index)} · " + self.tr("翻转中"),
            )
        elif active is not None:
            state, left = (
                self.tr("算法判读中"),
                f"{row_label(active.row_index)} · " + self.tr("翻转后"),
            )
        else:
            state, left = self.tr("孵育中"), self.tr("待机")
        self.video_state.setText(state)
        self.video_left.setText(left)
        info = self._services.monitor.video_info()
        if info is None:
            right = ""
        else:
            fps = f"{self._fps:.0f}" if self._fps else (f"{info.fps:g}" if info.fps else "–")
            right = f"{info.width}×{info.height} · {fps} fps"
        self.video_right.setText(right)
        elapsed = "–" if view.last_elapsed_ms is None else f"{view.last_elapsed_ms / 1000:.1f} s"
        angle_value = self._angle if self._angle is not None else view.last_flip_angle_deg
        angle = "–" if angle_value is None else f"{angle_value:.1f}°"
        self.algo_info.setText(
            self.tr("算法判读 {elapsed} · 翻转角度 {angle}").format(
                elapsed=mono_span(elapsed), angle=mono_span(angle)
            )
        )

    # ------------------------------------------------------------------ 操作

    def _on_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.report_preview.set_active(index == TAB_REPORT)
        if index == TAB_IMAGES:
            self.images_tab.refresh()

    def _on_tube_clicked(self, tube_no: int) -> None:
        view = self._view
        if view is not None and view.tube(tube_no) is not None:
            self.tubeRequested.emit(view.experiment_id, tube_no)

    def _on_row_activated(self, row: int) -> None:
        view = self._view
        if view is not None and 0 <= row < len(view.tubes):
            self.tubeRequested.emit(view.experiment_id, view.tubes[row].tube_no)

    def _on_row_title_activated(self, row_index: int) -> None:
        """选择整排并播放对应录像；没有录像时清除旧画面并提示。"""
        self._selected_video_row = row_index
        self.plate.set_active_row(row_index)
        recordings = set(self._recordings)
        if row_index not in recordings:
            self.replay_player.show_message(self.tr("这个排没有录像"))
            self.replay_empty.setText(self.tr("这个排没有录像"))
            self.replay_empty.setVisible(True)
        else:
            self.replay_rows.blockSignals(True)
            self.replay_rows.setCurrentIndex(self.replay_rows.findData(row_index))
            self.replay_rows.blockSignals(False)
            self._load_inline(row_index, autoplay=True)
        self._render_video()

    def focus_tube(self, tube_no: int) -> None:
        """返回监控页时重新选中原管位。"""
        self._tube_focus = tube_no
        self.plate.set_current_tube(tube_no)
        row = (
            next(
                (index for index, tube in enumerate(self._view.tubes) if tube.tube_no == tube_no),
                None,
            )
            if self._view is not None
            else None
        )
        if row is not None:
            self.table.view.selectRow(row)

    def request_stop(self) -> bool:
        """弹出确认框，确认后停止试验。

        Returns:
            已提交停止请求时为 True。
        """
        view = self._view
        if view is None or not view.can_stop:
            return False
        confirmed = ConfirmDialog.ask(
            self,
            self.tr("停止实验？"),
            self.tr(
                "停止后实验 {id} 不能继续，还没读数的排会错过读数时间；"
                "已经翻转的排会复位。确定要停止吗？"
            ).format(id=view.experiment_id),
            confirm_text=self.tr("停止实验"),
            cancel_text=self.tr("继续实验"),
            danger=True,
        )
        if not confirmed:
            return False
        try:
            self._services.monitor.stop(self._session, view.experiment_id, "用户在试验监控页停止")
        except AuthError as error:
            Toast.show_message(self, describe_auth_error(error), ToastKind.WARNING)
            return False
        except ServiceError as error:
            Toast.show_message(self, describe_service_error(error), ToastKind.WARNING)
            return False
        self.stop_button.setEnabled(False)
        Toast.show_message(self, self.tr("已请求停止，正在收尾"), ToastKind.INFO)
        return True

    def request_resume(self) -> bool:
        """弹出确认框，确认后恢复异常的试验。

        读数失败的排会重新翻转，翻动过的凝胶状态可能已变（PLAN 第 7 节第 15 项），所以必须由人确认。
        恢复条件不满足时引擎发告警，显示在横幅里，试验保持异常。

        Returns:
            已提交恢复请求时为 True。
        """
        view = self._view
        if view is None or not view.can_resume:
            return False
        confirmed = ConfirmDialog.ask(
            self,
            self.tr("恢复实验？"),
            self.tr(
                "恢复前请确认设备和温度已正常。读数失败的排会重新翻转，翻动过的凝胶状态可能已变；"
                "分析失败的排用保留的原图重试。确定要恢复吗？"
            ),
            confirm_text=self.tr("恢复实验"),
            cancel_text=self.tr("暂不恢复"),
        )
        if not confirmed:
            return False
        return self._run_recovery(self._services.monitor.resume, self.tr("已请求恢复实验"))

    def request_retry(self) -> bool:
        """重试分析失败的排：用保留的原图重新分析，不翻转。

        Returns:
            已提交重试请求时为 True。
        """
        view = self._view
        if view is None or not view.retry_rows:
            return False
        return self._run_recovery(self._services.monitor.retry_analysis, self.tr("已请求重试分析"))

    def _run_recovery(self, action: Callable[[Session | None, str], None], done: str) -> bool:
        view = self._view
        assert view is not None
        try:
            action(self._session, view.experiment_id)
        except AuthError as error:
            Toast.show_message(self, describe_auth_error(error), ToastKind.WARNING)
            return False
        except ServiceError as error:
            Toast.show_message(self, describe_service_error(error), ToastKind.WARNING)
            return False
        Toast.show_message(self, done, ToastKind.INFO)
        self._reload_pending = True
        self._request_flush()
        return True

    def open_replay(self) -> None:
        """打开按排回放对话框（默认最近有录像的一排）。"""
        view = self._view
        if view is None or not self._recordings:
            return
        dialog = ReplayDialog(
            self._services.media,
            self._session,
            view.experiment_id,
            self._recordings,
            self._recordings[-1],
            self,
        )
        self._replay_dialog = dialog
        dialog.show()

    def replay_dialog(self) -> ReplayDialog | None:
        """最近打开的回放对话框。

        Returns:
            对话框，没有时为 None。
        """
        return self._replay_dialog

    def _on_replay_row(self, _index: int) -> None:
        row = self.replay_rows.currentData()
        if row is not None:
            self._selected_video_row = int(row)
            self.plate.set_active_row(int(row))
            self._load_inline(int(row), autoplay=True)
            self._render_video()

    def _load_inline(self, row: int | None, *, autoplay: bool) -> None:
        view = self._view
        if view is None or row is None:
            return
        try:
            clip = self._services.media.open_clip(self._session, view.experiment_id, int(row))
        except ServiceError as error:
            self.replay_player.show_message(describe_service_error(error))
            return
        except AuthError as error:
            self.replay_player.show_message(describe_auth_error(error))
            return
        self.replay_player.load(clip, autoplay=autoplay)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        for index, text in enumerate(
            (
                self.tr("实验数据记录"),
                self.tr("图像显示"),
                self.tr("报告打印预览"),
            )
        ):
            self.tabs.set_tab_text(index, text)
        self.empty_title.setText(self.tr("当前没有进行中的实验"))
        self.empty_hint.setText(
            self.tr(
                "在“新建实验”里启动实验后，这里显示实时画面、孔板和结果；也可以到历史记录回看已完成的实验。"
            )
        )
        self.empty_history.setText(self.tr("打开历史记录"))
        self.stop_button.setText(self.tr("停止实验"))
        self.resume_button.setText(self.tr("恢复实验"))
        self.retry_button.setText(self.tr("重试分析"))
        for key, text in (
            ("lal", self.tr("{kind}批号").format(kind=reagent_kind_label(ReagentKind.LAL_REAGENT))),
            (
                "endotoxin",
                self.tr("{kind}批号").format(kind=reagent_kind_label(ReagentKind.ENDOTOXIN)),
            ),
            ("water", self.tr("{kind}批号").format(kind=reagent_kind_label(ReagentKind.WATER))),
            ("lambda", self.tr("标示灵敏度 λ")),
            ("operator", self.tr("操作人")),
            ("started", self.tr("开始时间")),
        ):
            self._fact_labels[key].setText(text)
        self.plate_card.set_title(plate_title())
        self.video_card.set_title(self.tr("实时视频"))
        self.replay_card.set_title(self.tr("按排回放"))
        self.replay_empty.setText(self.tr("这个实验没有录像"))
        self.table_card.set_title(self.tr("实验数据记录"))
        self.table.set_columns(self._columns())
        self.table.set_empty_text(self.tr("没有试管"))
        self.report_headline.setText(self.tr("报告打印预览"))
        self.report_open.setText(self.tr("在报告中心打开"))
        self.save_template_button.setText(self.tr("另存为模板…"))
        self._render_report_tabs()
        self._signature = None
        if self._view is not None:
            self._render_header()
            self._render_facts()
            self._render_video()
            self._dirty = True
            self._flush()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def shutdown(self) -> None:
        """页面销毁前释放回放，之后不再响应引擎事件。"""
        self._closed = True
        self._view = None
        self._timer.stop()
        self.report_preview.set_active(False)
        self.report_preview.set_context(None, None, None)
        self.replay_player.unload()
        if self._replay_dialog is not None:
            self._replay_dialog.close()
