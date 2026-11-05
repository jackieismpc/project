"""单管详情页：按 ``docs/design/TubeDetail.dc.html`` 实现。

翻转前后的裁剪图（后台线程读盘）、可开关的算法掩码叠加、3 帧序列、ΔA / Δs / σ_A / σ_s / Score、
三态、判读来源，以及人工复核。复核由 :class:`~gelinspect.services.review_service.ReviewService`
校验权限和状态并写审计；算法的原始结果不被覆盖，界面同时显示「算法：部分凝固 → 人工：阳性」。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.entities import TubeResult
from gelinspect.domain.enums import Interpretation, TubeAnalysisStatus
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import row_label, tube_to_position
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.media_service import TubeImages
from gelinspect.services.monitor_service import ExperimentView, TubeRow
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    role_label,
)
from gelinspect.ui.pages.monitor_format import (
    analysis_status_label,
    experiment_title,
    gel_state_label,
    interpretation_label,
    interpretation_state,
    number,
)
from gelinspect.ui.pages.monitor_images import warning_text
from gelinspect.ui.pages.replay_dialog import ReplayDialog
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.imaging import bgr_to_qimage
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.marks import WellState, paint_well
from gelinspect.ui.widgets.toast import Toast, ToastKind
from gelinspect.ui.widgets.tube_image_view import TubeImageView
from gelinspect.ui.widgets.video_view import VideoView

_FEATURE_KEYS = ("delta_area_pct", "delta_s", "sigma_area", "sigma_axial")


class StateMark(QWidget):
    """判读标记：与孔板相同的形状，36px。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建标记。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._state = WellState.EMPTY
        self.setFixedSize(36, 36)

    def set_state(self, state: WellState) -> None:
        """设置显示状态。

        Args:
            state: 孔状态。
        """
        self._state = state
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制。

        Args:
            event: 绘制事件。
        """
        p = QPainter(self)
        paint_well(p, QRectF(0, 0, 36, 36), self._state, current_tokens())
        p.end()


class ScoreBar(QWidget):
    """融合评分条（0–1）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建评分条。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._value: float | None = None
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: float | None) -> None:
        """设置评分。

        Args:
            value: 0–1，None 表示没有。
        """
        self._value = value
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            尺寸。
        """
        return QSize(200, 10)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制轨道和填充。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.track))
        p.drawRoundedRect(QRectF(self.rect()), 5, 5)
        if self._value is not None:
            width = self.width() * max(0.0, min(1.0, self._value))
            p.setBrush(QColor(c.review))
            p.drawRoundedRect(QRectF(0, 0, width, self.height()), 5, 5)
        p.end()


class MetricTile(QFrame):
    """数值块：标签加数值（等宽 22px），浅色底。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建数值块。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self._label = Label("", "lab")
        self._value = Label("", "stat", mono=True)
        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_content(self, label: str, value: str, unit: str = "") -> None:
        """设置内容。

        Args:
            label: 说明。
            value: 数值文字。
            unit: 单位。
        """
        self._label.setText(label)
        self._value.setText(f"{value} {unit}".strip() if value != "–" else value)
        self.setAccessibleName(f"{label} {self._value.text()}")


def _clock(value: datetime | None) -> str:
    return "" if value is None else value.astimezone().strftime("%H:%M:%S")


class TubeDetailPage(QWidget):
    """单管详情页。

    Signals:
        backRequested: 点击「返回孔板」。
        reviewed: 复核已提交，参数为试验标识。
    """

    backRequested = Signal()  # noqa: N815
    reviewed = Signal(str)

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._view: ExperimentView | None = None
        self._tube_no = 0
        self._images: TubeImages | None = None
        self._runner = BackgroundRunner(self)
        self._token: int | None = None
        self._pick: Interpretation | None = None
        self._replay_dialog: ReplayDialog | None = None
        self._submitted_note = ""

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
        self.back_button = Button("")
        self.back_button.clicked.connect(self.backRequested)
        self.title = Label("", "h1")
        self.meta = Label("", "meta")
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.user_label = Label("", "meta")
        top.addWidget(self.back_button)
        top.addWidget(self.title)
        top.addWidget(self.meta)
        top.addStretch(1)
        top.addWidget(self.user_label)
        col.addLayout(top)

        self.empty = Label("", "body")
        self.empty.setWordWrap(True)
        col.addWidget(self.empty)

        self.body = QWidget()
        work = QHBoxLayout(self.body)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(14)
        work.addWidget(self._build_compare(), 2)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_verdict())
        right.addWidget(self._build_review())
        right.addWidget(self._build_source())
        right.addStretch(1)
        work.addLayout(right, 1)
        col.addWidget(self.body)
        col.addStretch(1)

        self.mask_toggle.toggled.connect(self._on_mask_toggled)
        self.sequence_button.clicked.connect(self._toggle_sequence)
        self.replay_button.clicked.connect(self.open_replay)
        self.positive_button.clicked.connect(lambda: self._choose(Interpretation.POSITIVE))
        self.negative_button.clicked.connect(lambda: self._choose(Interpretation.NEGATIVE))
        self.submit_button.clicked.connect(self.submit_review)
        self.note.textChanged.connect(self._clear_error)
        self.retranslate()

    # ------------------------------------------------------------------ 搭建

    def _build_compare(self) -> Card:
        card = Card(" ")
        self.compare_card = card
        assert card.header is not None
        self.mask_toggle = QCheckBox()
        self.mask_toggle.setChecked(True)
        card.header.addWidget(self.mask_toggle)
        pair = QHBoxLayout()
        pair.setSpacing(12)
        self.before_view = TubeImageView()
        self.after_view = TubeImageView()
        self.before_caption = Label("", "meta", mono=True)
        self.after_caption = Label("", "meta", mono=True)
        for view, caption in (
            (self.before_view, self.before_caption),
            (self.after_view, self.after_caption),
        ):
            cell = QVBoxLayout()
            cell.setSpacing(6)
            cell.addWidget(view, 1)
            cell.addWidget(caption)
            pair.addLayout(cell, 1)
        card.body.addLayout(pair)
        self.image_warning = Label("", "danger")
        self.image_warning.setWordWrap(True)
        self.image_warning.setVisible(False)
        card.body.addWidget(self.image_warning)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.replay_button = Button("")
        self.sequence_button = Button("")
        buttons.addWidget(self.replay_button)
        buttons.addWidget(self.sequence_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        self.sequence_box = QWidget()
        grid = QGridLayout(self.sequence_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.sequence_views: list[VideoView] = []
        for index in range(6):
            frame_view = VideoView(min_height=120)
            self.sequence_views.append(frame_view)
            grid.addWidget(frame_view, index // 3, index % 3)
        self.sequence_box.setVisible(False)
        card.body.addWidget(self.sequence_box)
        card.body.addStretch(1)
        return card

    def _build_verdict(self) -> Card:
        card = Card()
        head = QHBoxLayout()
        head.setSpacing(12)
        self.mark = StateMark()
        text = QVBoxLayout()
        text.setSpacing(0)
        self.verdict_title = Label("", "val")
        self.verdict_title.setWordWrap(True)
        self.verdict_sub = Label("", "meta")
        self.verdict_sub.setWordWrap(True)
        text.addWidget(self.verdict_title)
        text.addWidget(self.verdict_sub)
        head.addWidget(self.mark)
        head.addLayout(text, 1)
        card.body.addLayout(head)
        score_row = QHBoxLayout()
        self.score_label = Label("", "meta")
        self.score_value = Label("", "meta", mono=True)
        score_row.addWidget(self.score_label)
        score_row.addStretch(1)
        score_row.addWidget(self.score_value)
        card.body.addLayout(score_row)
        self.score_bar = ScoreBar()
        card.body.addWidget(self.score_bar)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.tiles = [MetricTile() for _ in range(4)]
        for index, tile in enumerate(self.tiles):
            grid.addWidget(tile, index // 2, index % 2)
        card.body.addLayout(grid)
        self.status_note = Label("", "warn")
        self.status_note.setWordWrap(True)
        self.status_note.setVisible(False)
        card.body.addWidget(self.status_note)
        return card

    def _build_review(self) -> Card:
        card = Card(" ")
        self.review_card = card
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.positive_button = Button("")
        self.negative_button = Button("")
        for button in (self.positive_button, self.negative_button):
            button.setCheckable(True)
            buttons.addWidget(button)
        card.body.addLayout(buttons)
        self.note_label = Label("", "lab")
        self.note = QPlainTextEdit()
        self.note.setMinimumHeight(68)
        self.note.setMaximumHeight(110)
        self.submit_button = Button("", ButtonVariant.PRIMARY)
        self.review_error = Label("", "danger")
        self.review_error.setWordWrap(True)
        self.review_error.setVisible(False)
        self.review_hint = Label("", "meta")
        self.review_hint.setWordWrap(True)
        self.review_hint.setVisible(False)
        self.reviewed_box = Label("", "body")
        self.reviewed_box.setWordWrap(True)
        self.reviewed_box.setVisible(False)
        for widget in (
            self.reviewed_box,
            self.note_label,
            self.note,
            self.review_error,
            self.review_hint,
        ):
            card.body.addWidget(widget)
        card.body.addWidget(self.submit_button, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _build_source(self) -> Card:
        card = Card(" ")
        self.source_card = card
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.source_keys = [Label("", "meta") for _ in range(3)]
        self.source_values = [Label("", "body", mono=True) for _ in range(3)]
        for index in range(3):
            grid.addWidget(self.source_keys[index], index, 0)
            grid.addWidget(self.source_values[index], index, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    # ------------------------------------------------------------------ 打开

    def set_session(self, session: Session | None) -> None:
        """设置当前会话。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        if session is not None:
            user = session.user
            self.user_label.setText(f"{user.display_name} · {role_label(user.role)}")
        else:
            self.user_label.setText("")
        self._render_review()

    @property
    def tube_no(self) -> int:
        """正在显示的管号，没有打开时为 0。"""
        return self._tube_no

    @property
    def experiment_id(self) -> str | None:
        """正在显示的试验。"""
        return self._view.experiment_id if self._view is not None else None

    @property
    def images(self) -> TubeImages | None:
        """已读到的图像（后台加载完成后才有）。"""
        return self._images

    def open_tube(self, experiment_id: str, tube_no: int) -> bool:
        """显示某支试管。

        Args:
            experiment_id: 试验标识。
            tube_no: 管号。

        Returns:
            成功时为 True；试验或试管不存在、无权限时显示说明并返回 False。
        """
        self._runner.cancel()
        self._images = None
        self._view = None
        self._tube_no = 0
        self._pick = None
        self.note.clear()
        self.positive_button.setChecked(False)
        self.negative_button.setChecked(False)
        self.sequence_box.setVisible(False)
        self.before_view.set_content(None)
        self.after_view.set_content(None)
        self._clear_error()
        try:
            view = self._services.monitor.view(self._session, experiment_id)
        except (AuthError, ServiceError) as error:
            self._show_problem(
                describe_auth_error(error)
                if isinstance(error, AuthError)
                else describe_service_error(error)
            )
            return False
        if view.tube(tube_no) is None:
            self._show_problem(self.tr("{no} 号管不属于这个试验").format(no=tube_no))
            return False
        self._view = view
        self._tube_no = tube_no
        self.empty.setVisible(False)
        self.body.setVisible(True)
        self._render()
        self._load_images()
        return True

    def reload(self) -> None:
        """重新读取试验视图（复核之后）。"""
        if self._view is None:
            return
        try:
            self._view = self._services.monitor.view(self._session, self._view.experiment_id)
        except (AuthError, ServiceError):
            return
        self._render()

    def _show_problem(self, text: str) -> None:
        self.body.setVisible(False)
        self.empty.setText(text)
        self.empty.setVisible(True)
        self.title.setText(self.tr("单管详情"))
        self.meta.setText("")

    def _load_images(self) -> None:
        view = self._view
        if view is None:
            return
        session, experiment_id, tube = self._session, view.experiment_id, self._tube_no
        self.before_view.set_text(self.tr("正在读取图像…"))
        self.after_view.set_text(self.tr("正在读取图像…"))
        self._token = self._runner.submit(
            lambda: self._services.media.tube_images(session, experiment_id, tube),
            self._on_images,
            self._on_images_failed,
        )

    def _on_images(self, images: TubeImages) -> None:
        if images.tube_no != self._tube_no:
            return
        self._images = images
        text = self.tr("这支试管没有抓拍图像")
        for side, view in ((images.before, self.before_view), (images.after, self.after_view)):
            view.set_text(text)
            if side is None:
                view.set_content(None)
            else:
                view.set_content(side.image, side.gel, side.tube)
        warning = warning_text(images.warnings)
        self.image_warning.setText(warning)
        self.image_warning.setVisible(bool(warning))
        for views, frames in (
            (self.sequence_views[:3], images.sequence_before),
            (self.sequence_views[3:], images.sequence_after),
        ):
            for index, frame_view in enumerate(views):
                frame_view.set_image(bgr_to_qimage(frames[index]) if index < len(frames) else None)
                frame_view.set_text(self.tr("没有这一帧"))
        self._render_captions()

    def _on_images_failed(self, error: Exception) -> None:
        text = (
            describe_service_error(error)
            if isinstance(error, ServiceError)
            else self.tr("读取图像失败")
        )
        self.before_view.set_text(text)
        self.after_view.set_text(text)

    # ------------------------------------------------------------------ 显示

    def _row(self) -> TubeRow | None:
        return self._view.tube(self._tube_no) if self._view is not None else None

    def _result(self) -> TubeResult | None:
        row = self._row()
        return row.result if row is not None else None

    def _render(self) -> None:
        view, row = self._view, self._row()
        if view is None or row is None:
            return
        pos = tube_to_position(row.tube_no)
        self.title.setText(self.tr("{no} 号管").format(no=row.tube_no))
        spike = (
            ""
            if row.spiked_endotoxin is None
            else self.tr(" · 外加内毒素 {v} EU/ml").format(v=f"{row.spiked_endotoxin:g}")
        )
        name = experiment_title(view)
        self.meta.setText(
            f"{row_label(pos.row_index)} · {name}{spike} · "
            + self.tr("试验 {id}").format(id=view.experiment_id)
        )
        result = row.result
        self._render_verdict(result)
        self._render_source(result)
        self._render_review()
        self._render_captions()

    def _render_verdict(self, result: TubeResult | None) -> None:
        for tile, key, label, digits, unit in zip(
            self.tiles,
            _FEATURE_KEYS,
            (
                self.tr("面积变化率 ΔA"),
                self.tr("质心偏移 Δs"),
                self.tr("面积波动 σ_A"),
                self.tr("位置波动 σ_s"),
            ),
            (1, 1, 3, 3),
            ("%", "", "", ""),
            strict=True,
        ):
            raw = None if result is None else result.features.get(key)
            tile.set_content(label, number(raw if isinstance(raw, float) else None, digits), unit)
        if result is None:
            self.mark.set_state(WellState.EMPTY)
            self.verdict_title.setText(self.tr("暂无结果"))
            self.verdict_sub.setText(self.tr("这支试管还没有分析结果"))
            self.score_bar.set_value(None)
            self.score_value.setText("–")
            self.status_note.setVisible(False)
            return
        reviewed = result.reviewed_interpretation
        algorithm = gel_state_label(result.gel_state)
        if reviewed is not None:
            self.mark.set_state(interpretation_state(reviewed))
            self.verdict_title.setText(
                self.tr("算法：{algo} → 人工：{human}").format(
                    algo=algorithm, human=interpretation_label(reviewed)
                )
            )
            self.verdict_sub.setText(self.tr("已人工复核，以人工结论为准；算法原始结果保留"))
        else:
            self.mark.set_state(interpretation_state(result.auto_interpretation))
            self.verdict_title.setText(algorithm)
            if result.needs_review:
                sub = self.tr("算法结果 · 需要人工复核后才能签署")
            else:
                sub = self.tr("算法结果 · {v}").format(
                    v=interpretation_label(result.auto_interpretation)
                )
            self.verdict_sub.setText(sub)
        self.score_bar.set_value(result.score)
        self.score_value.setText(number(result.score, 2))
        if result.analysis_status is not TubeAnalysisStatus.OK:
            note = analysis_status_label(result.analysis_status)
            if result.message:
                note += f"：{result.message}"
            self.status_note.setText(note)
            self.status_note.setVisible(True)
        else:
            self.status_note.setVisible(False)

    def _render_source(self, result: TubeResult | None) -> None:
        elapsed = (
            "–"
            if result is None or result.elapsed_ms is None
            else self.tr("{s} s（本排 {n} 支）").format(
                s=f"{result.elapsed_ms / 1000:.1f}", n=self._row_size()
            )
        )
        values = [
            (result.model_version if result else None) or "–",
            (result.config_version if result else None) or "–",
            elapsed,
        ]
        for label, value in zip(self.source_values, values, strict=True):
            label.setText(value)

    def _row_size(self) -> int:
        row = self._row()
        if self._view is None or row is None:
            return 0
        return sum(1 for t in self._view.tubes if t.row_index == row.row_index)

    def _render_captions(self) -> None:
        result = self._result()
        angle = result.features.get("flip_angle_deg") if result is not None else None
        before = self.tr("翻转前") + " · 0.0°"
        after = self.tr("翻转后") + (f" · {angle:.1f}°" if isinstance(angle, float) else "")
        images = self._images
        if images is not None and images.before is not None:
            before += f"    {_clock(images.before.recorded_at)}"
        if images is not None and images.after is not None:
            after += f"    {_clock(images.after.recorded_at)}"
        self.before_caption.setText(before)
        self.after_caption.setText(after)
        self.replay_button.setEnabled(self._has_recording())

    def _has_recording(self) -> bool:
        row = self._row()
        if self._view is None or row is None:
            return False
        try:
            return row.row_index in self._services.media.recordings(
                self._session, self._view.experiment_id
            )
        except AuthError:
            return False

    # ------------------------------------------------------------------ 复核

    def _render_review(self) -> None:
        view, result = self._view, self._result()
        can_review = self._session is not None and self._session.can(Permission.RESULT_REVIEW)
        open_state = view is not None and view.state is ExperimentState.PENDING_REVIEW
        editable = can_review and open_state and result is not None
        for widget in (
            self.positive_button,
            self.negative_button,
            self.note,
            self.submit_button,
        ):
            widget.setEnabled(editable)
        hint = ""
        if result is None:
            hint = self.tr("这支试管还没有分析结果，不能复核")
        elif not can_review:
            hint = self.tr("当前角色没有复核权限，只有分析员可以复核")
        elif not open_state:
            if view is not None and view.state in (
                ExperimentState.COMPLETED,
                ExperimentState.SIGNED,
            ):
                hint = self.tr("试验已无待复核的试管")
            else:
                hint = self.tr("全部排读数分析完成、试验进入待复核后才能复核")
        self.review_hint.setText(hint)
        self.review_hint.setVisible(bool(hint))
        if result is not None and result.reviewed_interpretation is not None:
            who = result.reviewed_by or "–"
            when = (
                result.reviewed_at.astimezone().strftime("%m-%d %H:%M")
                if result.reviewed_at
                else ""
            )
            self.reviewed_box.setText(
                self.tr("已由 {who} 于 {when} 复核为{v}。意见：{note}").format(
                    who=who,
                    when=when,
                    v=interpretation_label(result.reviewed_interpretation),
                    note=result.review_note or "–",
                )
            )
            self.reviewed_box.setVisible(True)
        else:
            self.reviewed_box.setVisible(False)

    def _choose(self, value: Interpretation) -> None:
        self._pick = value
        self.positive_button.setChecked(value is Interpretation.POSITIVE)
        self.negative_button.setChecked(value is Interpretation.NEGATIVE)
        self._clear_error()

    def _clear_error(self) -> None:
        self.review_error.setVisible(False)

    def is_dirty(self) -> bool:
        """是否有写了一半、还没提交的复核意见。

        Returns:
            有未提交的意见时为 True。
        """
        return bool(self.note.toPlainText().strip()) and self.note.isEnabled()

    def confirm_leave(self) -> bool:
        """离开页面前的确认：有未提交的复核意见时询问。

        Returns:
            可以离开时为 True。
        """
        if not self.is_dirty():
            return True
        return ConfirmDialog.ask(
            self,
            self.tr("放弃未提交的复核？"),
            self.tr("复核意见还没有提交，离开后会丢失。"),
            confirm_text=self.tr("放弃并离开"),
            cancel_text=self.tr("留在本页"),
            danger=True,
        )

    def submit_review(self) -> bool:
        """提交复核。校验失败时在按钮下方显示原因。

        Returns:
            复核已写入时为 True。
        """
        view = self._view
        if view is None:
            return False
        if self._pick is None:
            self._fail(self.tr("请先选择判为阳性或判为阴性"))
            return False
        try:
            outcome = self._services.reviews.review_tube(
                self._session,
                view.experiment_id,
                self._tube_no,
                self._pick,
                self.note.toPlainText(),
            )
        except AuthError as error:
            self._fail(describe_auth_error(error))
            return False
        except ServiceError as error:
            self._fail(describe_service_error(error))
            return False
        self.note.clear()
        self._pick = None
        self.positive_button.setChecked(False)
        self.negative_button.setChecked(False)
        self.reload()
        if self._session is not None:
            Toast.show_message(
                self,
                self.tr("复核已提交")
                if outcome.remaining
                else self.tr("全部待复核的试管已处理，试验可以签署"),
                ToastKind.SUCCESS,
            )
        self.reviewed.emit(view.experiment_id)
        return True

    def _fail(self, text: str) -> None:
        self.review_error.setText(text)
        self.review_error.setVisible(True)

    # ------------------------------------------------------------------ 其他操作

    def _on_mask_toggled(self, checked: bool) -> None:
        self.before_view.set_overlay_visible(checked)
        self.after_view.set_overlay_visible(checked)

    def mask_overlay_visible(self) -> bool:
        """掩码叠加开关的状态。

        Returns:
            打开时为 True。
        """
        return self.mask_toggle.isChecked()

    def _toggle_sequence(self) -> None:
        self.sequence_box.setVisible(not self.sequence_box.isVisible())
        self._retext_sequence_button()

    def _retext_sequence_button(self) -> None:
        self.sequence_button.setText(
            self.tr("收起 3 帧序列") if self.sequence_box.isVisible() else self.tr("查看 3 帧序列")
        )

    def open_replay(self) -> None:
        """回放这支试管所在排的翻转录像。"""
        view, row = self._view, self._row()
        if view is None or row is None:
            return
        rows = sorted(self._services.media.recordings(self._session, view.experiment_id))
        if row.row_index not in rows:
            return
        dialog = ReplayDialog(
            self._services.media, self._session, view.experiment_id, rows, row.row_index, self
        )
        self._replay_dialog = dialog
        dialog.show()

    def replay_dialog(self) -> ReplayDialog | None:
        """最近打开的回放对话框。

        Returns:
            对话框，没有时为 None。
        """
        return self._replay_dialog

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.back_button.setText(self.tr("返回孔板"))
        self.compare_card.set_title(self.tr("翻转前后对比"))
        self.mask_toggle.setText(self.tr("显示算法掩码"))
        self.replay_button.setText(self.tr("回放翻转视频"))
        self._retext_sequence_button()
        self.score_label.setText(self.tr("融合评分 Score"))
        self.review_card.set_title(self.tr("人工复核"))
        self.positive_button.setText(self.tr("判为阳性"))
        self.negative_button.setText(self.tr("判为阴性"))
        self.note_label.setText(self.tr("复核意见（必填）"))
        self.note.setPlaceholderText(self.tr("说明判定依据，会写入审计追踪"))
        self.submit_button.setText(self.tr("提交复核"))
        self.source_card.set_title(self.tr("判读来源"))
        for label, text in zip(
            self.source_keys,
            (self.tr("模型版本"), self.tr("阈值配置"), self.tr("判读耗时")),
            strict=True,
        ):
            label.setText(text)
        for view in (self.before_view, self.after_view):
            if not view.has_image():
                view.set_text(self.tr("暂无图像"))
        if self._view is not None:
            self._render()
        else:
            self.empty.setText(self.tr("从试验监控的孔板或结果表里点一支试管查看详情。"))
            self.empty.setVisible(True)
            self.body.setVisible(False)
            self.title.setText(self.tr("单管详情"))
        if self._session is not None:
            self.set_session(self._session)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def shutdown(self) -> None:
        """页面销毁前取消后台任务并关闭回放。"""
        self._runner.cancel()
        self._runner.wait(2000)
        if self._replay_dialog is not None:
            self._replay_dialog.close()
