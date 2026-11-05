"""工作台：仪器温度、进行中的试验、最近结果和需要处理的事项。

用真实组件搭骨架，数据通过 ``AppServices`` 读取：温度来自仪器状态和引擎事件，
试验列表来自 ``QueryService``（带权限校验）。没有数据时显示空状态。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from gelinspect.domain.permissions import Permission
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.dashboard_service import (
    AttentionItem,
    AttentionKind,
    RecentResult,
)
from gelinspect.services.engine_types import RowPhase, TemperatureChanged
from gelinspect.services.instrument_monitor import InstrumentStatus
from gelinspect.services.monitor_service import ExperimentView
from gelinspect.ui.labels import (
    experiment_state_label,
    experiment_type_label,
    reagent_kind_label,
)
from gelinspect.ui.pages.monitor_format import experiment_title, status_badge, well_state
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.mini_plate import MiniPlate
from gelinspect.ui.widgets.progress_bar import ProgressBar
from gelinspect.ui.widgets.sparkline import Sparkline
from gelinspect.ui.widgets.stat_card import ReadoutCard

TARGET_C = 37.0
TOLERANCE_C = 1.0
MAX_POINTS = 120
RUNNING_REFRESH_MS = 250
"""进行中试验卡片的最短刷新间隔。"""
"""曲线最多保留的采样点数。"""


def _local(value: datetime) -> str:
    return value.astimezone().strftime("%m-%d %H:%M")


class DashboardPage(QWidget):
    """工作台页面。

    Signals:
        navigationRequested: 请求跳转到某页面，参数为路由标识。
        experimentRequested: 请求打开某个试验（监控页），参数为试验标识。
    """

    navigationRequested = Signal(str)  # noqa: N815
    experimentRequested = Signal(str)  # noqa: N815

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._status: InstrumentStatus | None = None
        self._points: list[float] = []
        self._recent: list[RecentResult] = []
        self._attention: list[AttentionItem] = []
        self._running: ExperimentView | None = None
        self._denied = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RUNNING_REFRESH_MS)
        self._timer.timeout.connect(self._refresh_running)

        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        col = QVBoxLayout(page)
        col.setContentsMargins(24, 20, 24, 28)
        col.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.instrument_label = Label("", "meta")
        self.new_button = Button("", ButtonVariant.PRIMARY, icon="circle-plus")
        self.new_button.clicked.connect(lambda: self.navigationRequested.emit("new"))
        top.addWidget(self.title)
        top.addWidget(self.instrument_label)
        top.addStretch(1)
        top.addWidget(self.new_button)
        col.addLayout(top)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        self.sparkline = Sparkline(compact=False)
        self.temperature = ReadoutCard("", "--", "℃", sparkline=self.sparkline)
        self.temperature.body.addStretch(1)
        row1.addWidget(self.temperature, 2)
        self.running_card = Card(" ")
        self.running_state = StatusBadge("", BadgeTone.INFO)
        self.running_id = Label("", "meta", mono=True)
        self.running_title = Label("", "val")
        self.running_title.setWordWrap(True)
        self.running_rows = Label("", "meta")
        self.mini_plate = MiniPlate()
        self.progress = ProgressBar()
        self.running_open = Button("")
        self.running_open.clicked.connect(lambda: self.navigationRequested.emit("monitor"))
        assert self.running_card.header is not None
        self.running_card.header.addWidget(self.running_id)
        self.running_card.body.addWidget(self.running_state, 0, Qt.AlignmentFlag.AlignLeft)
        running_row = QHBoxLayout()
        running_row.setSpacing(16)
        running_row.addWidget(self.mini_plate, 0, Qt.AlignmentFlag.AlignTop)
        running_text = QVBoxLayout()
        running_text.setSpacing(6)
        for w in (self.running_title, self.running_rows, self.progress):
            running_text.addWidget(w)
        running_text.addStretch(1)
        running_row.addLayout(running_text, 1)
        self.running_card.body.addLayout(running_row)
        self.running_card.body.addWidget(self.running_open, 0, Qt.AlignmentFlag.AlignLeft)
        self.running_empty = Label("", "meta")
        self.running_empty.setWordWrap(True)
        self.running_card.body.addWidget(self.running_empty)
        self.running_card.body.addStretch(1)
        row1.addWidget(self.running_card, 1)
        col.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        self.recent_card = Card(" ")
        self.recent_table = DataTable(self._columns(), fit_content=True)
        self.recent_table.rowActivated.connect(self._on_recent_activated)
        self.recent_card.body.addWidget(self.recent_table)
        row2.addWidget(self.recent_card, 2)
        self.attention_card = Card(" ")
        self.attention_box = QVBoxLayout()
        self.attention_box.setSpacing(10)
        self.attention_card.body.addLayout(self.attention_box)
        self.attention_card.body.addStretch(1)
        row2.addWidget(self.attention_card, 1)
        col.addLayout(row2)
        col.addStretch(1)
        self.retranslate()

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        """重新读取进行中的试验、最近结果和待处理事项。无权限时显示空状态。"""
        self._denied = False
        self._recent, self._attention, self._running = [], [], None
        if self._session is not None:
            try:
                dashboard = self._services.dashboard
                self._recent = dashboard.recent_results(self._session, 8)
                self._attention = dashboard.attention(self._session)
                self._running = dashboard.running(self._session)
                if not self._points:
                    self._points = dashboard.temperatures(self._session)[-MAX_POINTS:]
                    self.sparkline.set_values(self._points)
            except AuthError:
                self._denied = True
        self._render()

    def request_running_refresh(self) -> None:
        """孔和倒计时事件很密：合并成每 250 ms 最多一次的刷新。"""
        if not self._timer.isActive():
            self._timer.start()

    def _refresh_running(self) -> None:
        if self._session is None:
            return
        try:
            self._running = self._services.dashboard.running(self._session)
        except AuthError:
            self._running = None
        self._render_running()

    def on_instrument(self, status: InstrumentStatus) -> None:
        """收到仪器状态（空闲时的探测结果）。

        Args:
            status: 仪器状态。
        """
        self._status = status
        if status.temperature_c is not None:
            self._push(status.temperature_c)
        self._render_temperature()

    def on_temperature(self, event: TemperatureChanged) -> None:
        """收到试验期间的温度读数。

        Args:
            event: 温度事件。
        """
        self._status = InstrumentStatus(event.celsius is not None, event.celsius, event.at)
        if event.celsius is not None:
            self._push(event.celsius)
        self._render_temperature()

    def _push(self, celsius: float) -> None:
        self._points.append(celsius)
        del self._points[:-MAX_POINTS]
        self.sparkline.set_values(self._points)

    # ------------------------------------------------------------------ 显示

    def _columns(self) -> list[Column]:
        return [
            Column("id", self.tr("试验"), mono=True),
            Column("type", self.tr("类型")),
            Column("tubes", self.tr("试管"), mono=True, align_right=True),
            Column("result", self.tr("结果")),
            Column("state", self.tr("状态")),
            Column("completed", self.tr("完成时间"), mono=True),
        ]

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("工作台"))
        self.new_button.setText(self.tr("新建试验"))
        self.new_button.setAccessibleName(self.tr("新建试验"))
        self.temperature.set_label(self.tr("检测室温度"))
        self.running_card.set_title(self.tr("进行中的试验"))
        self.running_open.setText(self.tr("打开监控"))
        self.recent_card.set_title(self.tr("最近结果"))
        self.attention_card.set_title(self.tr("需要处理"))
        self.recent_table.set_columns(self._columns())
        self.recent_table.set_empty_text(
            self.tr("还没有历史试验"), self.tr("完成第一次试验后，结果会显示在这里")
        )
        self._render()

    def _render(self) -> None:
        self._render_header()
        self._render_temperature()
        self._render_running()
        self._render_recent()
        self._render_attention()

    def _render_header(self) -> None:
        can_create = self._session is not None and self._session.can(Permission.EXPERIMENT_CREATE)
        self.new_button.setEnabled(can_create)
        self.new_button.setToolTip("" if can_create else self.tr("当前角色无此权限"))

    def _render_temperature(self) -> None:
        status = self._status
        if status is None or not status.connected or status.temperature_c is None:
            self.temperature.set_value("--", "℃")
            self.temperature.set_note(self.tr("仪器未连接"))
            self.instrument_label.setText(self.tr("仪器未连接"))
            return
        value = status.temperature_c
        ok = abs(value - TARGET_C) <= TOLERANCE_C
        self.temperature.set_value(f"{value:.1f}", "℃")
        verdict = self.tr("正常") if ok else self.tr("偏离范围")
        colors = current_tokens().colors
        color = colors.success if ok else colors.danger
        self.temperature.set_note(
            self.tr("要求 37 ± 1 ℃ · ") + f"<span style='color:{color}'>{verdict}</span>"
        )
        self.instrument_label.setText(self.tr("仪器已连接"))

    def _render_running(self) -> None:
        view = self._running
        running = view is not None
        for w in (
            self.running_state,
            self.mini_plate,
            self.running_title,
            self.running_rows,
            self.progress,
            self.running_open,
        ):
            w.setVisible(running)
        self.running_empty.setVisible(not running)
        if view is None:
            self.running_id.setText("")
            self.running_empty.setText(self.tr("当前没有进行中的试验。"))
            return
        self.running_id.setText(view.experiment_id)
        text, tone = status_badge(view)
        self.running_state.set_text(text)
        self.running_state.set_tone(tone)
        self.mini_plate.set_states({t.tube_no: well_state(t.status) for t in view.tubes})
        done = sum(1 for r in view.rows if r.phase is RowPhase.DONE)
        total = len(view.rows)
        self.progress.set_value(done / total if total else 0.0)
        name = experiment_title(view)
        self.running_title.setText(name)
        self.running_rows.setText(
            self.tr("{tubes} 管 · 已完成 {done} / {total} 排").format(
                tubes=len(view.tubes), done=done, total=total
            )
        )

    def _result_text(self, item: RecentResult) -> str:
        if not item.finished:
            return ""
        if item.pending:
            return self.tr("{n} 管待复核").format(n=item.pending)
        if item.positives == 0:
            return self.tr("全部阴性")
        return self.tr("阳性 {p} · 阴性 {n}").format(p=item.positives, n=item.negatives)

    def _render_recent(self) -> None:
        self.recent_table.set_rows(
            [
                {
                    "id": s.experiment_id,
                    "type": experiment_type_label(s.experiment_type),
                    "tubes": s.tube_count,
                    "result": self._result_text(s),
                    "state": experiment_state_label(s.state),
                    "completed": "" if s.completed_at is None else _local(s.completed_at),
                }
                for s in self._recent
            ]
        )

    def _on_recent_activated(self, row: int) -> None:
        if 0 <= row < len(self._recent):
            self.experimentRequested.emit(self._recent[row].experiment_id)

    def _attention_line(self, item: AttentionItem) -> tuple[str, str, str | None]:
        """（样式角色, 文字, 点击后打开的试验）。"""
        if item.kind is AttentionKind.REVIEW_PENDING:
            text = self.tr("试验 {id} 有 {n} 管待复核").format(id=item.experiment_id, n=item.count)
            return "link", text, item.experiment_id
        if item.kind is AttentionKind.FAULTED:
            text = self.tr("试验 {id} 处于异常状态，需要人工处理").format(id=item.experiment_id)
            return "link", text, item.experiment_id
        assert item.label is not None
        name = f"{reagent_kind_label(item.label)} {item.batch_no}"
        if item.kind is AttentionKind.REAGENT_EXPIRED:
            return "danger", self.tr("{name} 已过期 {n} 天").format(name=name, n=item.count), None
        return "warn", self.tr("{name} 还有 {n} 天过期").format(name=name, n=item.count), None

    def _render_attention(self) -> None:
        while self.attention_box.count():
            item = self.attention_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if self._denied:
            self.attention_box.addWidget(Label(self.tr("当前角色无此权限"), "meta"))
            return
        if not self._attention:
            self.attention_box.addWidget(Label(self.tr("没有需要处理的事项"), "ok"))
            return
        for entry in self._attention:
            role, text, target = self._attention_line(entry)
            if target is not None:
                button = Button(text)
                button.setProperty("role", "link")
                button.clicked.connect(lambda _c=False, t=target: self.experimentRequested.emit(t))
                self.attention_box.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            else:
                label = Label(text, role)
                label.setWordWrap(True)
                self.attention_box.addWidget(label)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
