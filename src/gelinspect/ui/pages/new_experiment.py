"""新试验向导：选模板 → 校验试剂 → 录入样品和管位 → 确认启动。

对照 ``docs/design/Wizard.dc.html``：四步胶囊步骤条；第 3 步左边是样品表、右边是 12 排×8 支管位
（选择模式）。页面只负责显示和收集输入；数量是否对得上、选中的管号如何对应样品行在
``services.experiment_draft``，试剂校验、创建和启动在 ``services`` 里，服务层会把权限、
试剂和数量**再校验一遍**，所以界面被绕过也不会放行。

试剂过期或未备案时锁定流程（红色说明原因，不能进入下一步），每次扫码都写审计。
返回上一步不丢数据；离开未完成的向导要确认。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import ReagentKind
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import HOLES_PER_ROW, ROW_COUNT, row_label, tube_to_position
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.engine_types import AlertCode, EngineAlert, StateChanged
from gelinspect.services.errors import ServiceError
from gelinspect.services.experiment_draft import ExperimentDraft
from gelinspect.services.reagent_service import ReagentLockedError, ReagentScan
from gelinspect.services.template_service import TemplateInfo
from gelinspect.ui.bridge import EngineBridge
from gelinspect.ui.labels import (
    builtin_name_text,
    describe_auth_error,
    describe_lock_reasons,
    describe_scan,
    describe_service_error,
    describe_storage_busy,
    experiment_type_label,
    reagent_kind_label,
    role_label,
)
from gelinspect.ui.pages.wizard_widgets import Banner, ReagentCard, ReagentTile, TemplateOption
from gelinspect.ui.punctuation import join_items
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.dialog import ConfirmDialog, NoticeDialog
from gelinspect.ui.widgets.edit_table import EditColumn, EditTable
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateMode
from gelinspect.ui.widgets.step_indicator import StepIndicator

_log = logging.getLogger(__name__)

STEP_TEMPLATE, STEP_REAGENTS, STEP_SAMPLES, STEP_CONFIRM = range(4)
"""四个步骤的序号。"""

TABLE_MAX_HEIGHT = 420
"""样品表的最大高度（设计稿 ``.tw``），超出时表内滚动。"""

COL_INDEX, COL_NAME, COL_BATCH, COL_SPIKE, COL_TUBE = range(5)
"""样品表的列。"""


def _format_number(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _parse_number(text: str) -> float | None:
    try:
        return float(text) if text.strip() else None
    except ValueError:
        return None


class NewExperimentPage(QWidget):
    """新试验向导页。

    Signals:
        started: 试验已启动（引擎进入孵育），参数为试验标识；主窗口据此跳到试验监控页。
    """

    started = Signal(str)

    def __init__(
        self, services: AppServices, bridge: EngineBridge, parent: QWidget | None = None
    ) -> None:
        """创建向导页。

        Args:
            services: 界面服务。
            bridge: 引擎事件桥，用来得知启动成功或失败。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._draft = ExperimentDraft()
        self._templates: list[TemplateInfo] = []
        self._scans: dict[ReagentKind, ReagentScan] = {}
        self._lock_dialog: NoticeDialog | None = None
        self._step = STEP_TEMPLATE
        self._created_id: str | None = None
        self._starting = False
        self._start_error: str | None = None
        self._confirm_error: Callable[[], str] | None = None
        self._syncing = False

        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        column = QVBoxLayout(page)
        column.setContentsMargins(24, 20, 24, 28)
        column.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.steps = StepIndicator(["", "", "", ""], 0)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.steps)
        column.addLayout(top)

        self.stack = QStackedWidget()
        column.addWidget(self.stack)
        column.addStretch(1)
        self._build_template_step()
        self._build_reagent_step()
        self._build_sample_step()
        self._build_confirm_step()

        bridge.stateChanged.connect(self._on_state_changed)
        bridge.alertRaised.connect(self._on_alert)
        self.retranslate()
        self._show_step(STEP_TEMPLATE)

    # ================================================================== 构建

    def _nav_row(self, *, back: bool, next_variant: ButtonVariant = ButtonVariant.PRIMARY) -> Any:
        """一行导航按钮：左边留给额外按钮，右边是上一步和下一步。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        left = QHBoxLayout()
        left.setSpacing(8)
        row.addLayout(left)
        row.addStretch(1)
        back_button = Button("", ButtonVariant.SECONDARY)
        back_button.setVisible(back)
        next_button = Button("", next_variant)
        row.addWidget(back_button)
        row.addWidget(next_button)
        return row, left, back_button, next_button

    def _build_template_step(self) -> None:
        step = QWidget()
        layout = QVBoxLayout(step)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.template_card = Card(" ")
        self.template_hint = Label("", "meta")
        self.template_hint.setWordWrap(True)
        self.template_card.body.addWidget(self.template_hint)
        self.template_grid = QGridLayout()
        self.template_grid.setSpacing(10)
        self.template_grid.setColumnStretch(0, 1)
        self.template_grid.setColumnStretch(1, 1)
        self.template_card.body.addLayout(self.template_grid)
        self.template_empty = Label("", "meta")
        self.template_empty.setWordWrap(True)
        self.template_card.body.addWidget(self.template_empty)
        row, _left, _back, self.template_next = self._nav_row(back=False)
        self.template_card.body.addLayout(row)
        layout.addWidget(self.template_card)
        layout.addStretch(1)
        self._options: list[TemplateOption] = []
        self.template_next.clicked.connect(lambda: self._go(STEP_REAGENTS))
        self.stack.addWidget(step)

    def _build_reagent_step(self) -> None:
        step = QWidget()
        layout = QVBoxLayout(step)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.lock_banner = Banner()
        layout.addWidget(self.lock_banner)
        self.reagent_intro = Label("", "meta")
        self.reagent_intro.setWordWrap(True)
        layout.addWidget(self.reagent_intro)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.reagent_cards: dict[ReagentKind, ReagentCard] = {}
        for index, kind in enumerate(ReagentKind):
            card = ReagentCard(kind)
            card.scanRequested.connect(self._on_scan_requested)
            self.reagent_cards[kind] = card
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        layout.addLayout(grid)
        row, _left, self.reagent_back, self.reagent_next = self._nav_row(back=True)
        layout.addLayout(row)
        layout.addStretch(1)
        self.reagent_back.clicked.connect(lambda: self._go(STEP_TEMPLATE))
        self.reagent_next.clicked.connect(lambda: self._go(STEP_SAMPLES))
        self.stack.addWidget(step)

    def _build_sample_step(self) -> None:
        step = QWidget()
        layout = QVBoxLayout(step)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        summary = Card()
        row = QHBoxLayout()
        row.setSpacing(14)
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)
        self.summary_lab = Label("", "lab")
        self.summary_line = Label("", "body")
        self.summary_line.setWordWrap(True)
        self.change_template = Button("", ButtonVariant.SECONDARY)
        self.change_template.setProperty("role", "link")
        self.change_template.setMinimumHeight(32)
        self.change_template.clicked.connect(lambda: self._go(STEP_TEMPLATE))
        info.addWidget(self.summary_lab)
        info.addWidget(self.summary_line)
        info.addWidget(self.change_template, 0, Qt.AlignmentFlag.AlignLeft)
        info.addStretch(1)
        info_box = QWidget()
        info_box.setLayout(info)
        info_box.setMinimumWidth(200)
        row.addWidget(info_box, 3)
        self.tiles: dict[ReagentKind, ReagentTile] = {}
        for kind in ReagentKind:
            tile = ReagentTile(kind)
            self.tiles[kind] = tile
            row.addWidget(tile, 2)
        summary.body.addLayout(row)
        layout.addWidget(summary)

        work = QHBoxLayout()
        work.setSpacing(14)
        self.sample_card = Card(" ")
        self.sample_table = EditTable(self._sample_columns())
        self.sample_table.setMaximumHeight(TABLE_MAX_HEIGHT)
        self.sample_table.setMinimumHeight(200)
        self.sample_table.cellEdited.connect(self._on_cell_edited)
        self.sample_card.body.addWidget(self.sample_table, 1)
        self.sample_problem = Label("", "warn")
        self.sample_problem.setWordWrap(True)
        self.sample_problem.setVisible(False)
        self.sample_card.body.addWidget(self.sample_problem)
        limits = QHBoxLayout()
        limits.setSpacing(12)
        self.mvd_label = Label("", "lab")
        self.mvd_edit = QLineEdit()
        self.mvd_edit.setMinimumHeight(44)
        self.limit_label = Label("", "lab")
        self.limit_edit = QLineEdit()
        self.limit_edit.setMinimumHeight(44)
        for label, edit in ((self.mvd_label, self.mvd_edit), (self.limit_label, self.limit_edit)):
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(label)
            box.addWidget(edit)
            limits.addLayout(box, 1)
        self.sample_card.body.addLayout(limits)
        self.limit_hint = Label("", "meta")
        self.limit_hint.setWordWrap(True)
        self.sample_card.body.addWidget(self.limit_hint)
        self.limit_problem = Label("", "warn")
        self.limit_problem.setWordWrap(True)
        self.limit_problem.setVisible(False)
        self.sample_card.body.addWidget(self.limit_problem)
        self.mvd_edit.textChanged.connect(self._on_limits_edited)
        self.limit_edit.textChanged.connect(self._on_limits_edited)
        work.addWidget(self.sample_card, 1)

        self.plate_card = Card(" ")
        assert self.plate_card.header is not None
        self.count_label = Label("", "meta")
        self.match_label = Label("", "ok")
        self.plate_card.header.addWidget(self.count_label)
        self.plate_card.header.addWidget(self.match_label)
        self.plate = PlateGrid(PlateMode.SELECT)
        self.plate.set_countdowns_visible(False)
        self.plate.set_row_labels_visible(False)
        self.plate.selectionChanged.connect(self._on_selection_changed)
        self.plate_card.body.addWidget(self.plate)
        row, left, self.sample_back, self.sample_next = self._nav_row(back=True)
        self.auto_button = Button("", ButtonVariant.SECONDARY)
        self.clear_button = Button("", ButtonVariant.SECONDARY)
        left.addWidget(self.auto_button)
        left.addWidget(self.clear_button)
        self.plate_card.body.addLayout(row)
        work.addWidget(self.plate_card, 1)
        layout.addLayout(work)
        layout.addStretch(1)
        self.auto_button.clicked.connect(self._auto_select)
        self.clear_button.clicked.connect(self.plate.clear_selection)
        self.sample_back.clicked.connect(lambda: self._go(STEP_REAGENTS))
        self.sample_next.clicked.connect(lambda: self._go(STEP_CONFIRM))
        self.stack.addWidget(step)

    def _build_confirm_step(self) -> None:
        step = QWidget()
        layout = QVBoxLayout(step)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.start_banner = Banner()
        layout.addWidget(self.start_banner)
        work = QHBoxLayout()
        work.setSpacing(14)
        self.info_card = Card(" ")
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(16)
        self.info_grid.setVerticalSpacing(8)
        self.info_grid.setColumnStretch(1, 1)
        self.info_card.body.addLayout(self.info_grid)
        self.info_card.body.addStretch(1)
        self.start_note = Label("", "meta")
        self.start_note.setWordWrap(True)
        self.info_card.body.addWidget(self.start_note)
        work.addWidget(self.info_card, 1)
        self.summary_card = Card(" ")
        self.summary_table = DataTable(self._summary_columns())
        self.summary_table.setMaximumHeight(TABLE_MAX_HEIGHT)
        self.summary_table.setMinimumHeight(340)
        self.summary_card.body.addWidget(self.summary_table, 1)
        work.addWidget(self.summary_card, 1)
        layout.addLayout(work)
        row, _left, self.confirm_back, self.confirm_start = self._nav_row(back=True)
        layout.addLayout(row)
        layout.addStretch(1)
        self.confirm_back.clicked.connect(self._back_from_confirm)
        self.confirm_start.clicked.connect(self._confirm)
        self.stack.addWidget(step)

    # ================================================================== 文字

    def _sample_columns(self) -> list[EditColumn]:
        return [
            EditColumn(self.tr("序号"), "readonly", mono=True),
            EditColumn(self.tr("样品名称"), "text"),
            EditColumn(self.tr("样品批号"), "text", mono=True, placeholder=self.tr("点击录入")),
            EditColumn(self.tr("外加内毒素"), "number", mono=True),
            EditColumn(self.tr("管号"), "readonly", mono=True),
        ]

    def _summary_columns(self) -> list[Column]:
        return [
            Column("i", self.tr("序号"), mono=True),
            Column("name", self.tr("样品名称")),
            Column("batch", self.tr("样品批号"), mono=True),
            Column("spike", self.tr("外加内毒素"), mono=True),
            Column("tube", self.tr("管号"), mono=True),
            Column("row", self.tr("排"), mono=True),
        ]

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("新试验"))
        self.steps.set_steps(
            [
                self.tr("选择模板"),
                self.tr("校验试剂"),
                self.tr("录入样品和管位"),
                self.tr("确认启动"),
            ]
        )
        self.template_card.set_title(self.tr("选择检测模板"))
        self.template_hint.setText(
            self.tr("模板决定试管数量和每管的样品、外加内毒素。标“布局待确认”的模板仍可使用。")
        )
        self.template_empty.setText(self.tr("还没有可用的模板"))
        self.template_next.setText(self.tr("下一步：校验试剂"))
        self.reagent_intro.setText(
            self.tr(
                "用扫码枪扫试剂上的码（扫完自动回车），或手输后按回车。三种试剂都合格才能继续。"
            )
        )
        self.reagent_back.setText(self.tr("上一步"))
        self.reagent_next.setText(self.tr("下一步：录入样品和管位"))
        self.summary_lab.setText(self.tr("模板"))
        self.change_template.setText(self.tr("更换模板"))
        self.sample_card.set_title(self.tr("样品"))
        self.mvd_label.setText(self.tr("MVD（可选）"))
        self.limit_label.setText(self.tr("内毒素限值 EU/mL（可选）"))
        self.mvd_edit.setPlaceholderText(self.tr("留空则报告里不填"))
        self.limit_edit.setPlaceholderText(self.tr("留空则报告里不填"))
        self.limit_hint.setText(self.tr("录入后写入试验记录并显示在报告里；试验签署后不能再修改。"))
        self.sample_table.set_columns(self._sample_columns())
        self.plate_card.set_title(self.tr("管位"))
        self.plate.setAccessibleName(
            self.tr("{rows} 排 × {holes} 支管位").format(rows=ROW_COUNT, holes=HOLES_PER_ROW)
        )
        self.auto_button.setText(self.tr("自动选择"))
        self.clear_button.setText(self.tr("全部清除"))
        self.sample_back.setText(self.tr("上一步"))
        self.sample_next.setText(self.tr("下一步：确认启动"))
        self.info_card.set_title(self.tr("试验信息"))
        self.start_note.setText(
            self.tr("确认后设备会连接并送入滑台，随后开始孵育。启动前会检查温度、舱门和视频。")
        )
        self.summary_card.set_title(self.tr("样品与管位"))
        self.summary_table.set_columns(self._summary_columns())
        self.summary_table.set_empty_text(self.tr("没有样品"))
        self.confirm_back.setText(self.tr("上一步"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    # ================================================================== 对外接口

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        """进入页面时重新读取模板（没有未完成的录入时），并按权限刷新按钮。"""
        if self._session is None:
            return
        self._load_templates()
        for kind, card in self.reagent_cards.items():
            card.set_samples(self._services.reagents.sample_codes(kind))
        self._render()

    def current_step(self) -> int:
        """当前步骤序号（0–3）。

        Returns:
            步骤序号。
        """
        return self._step

    def draft(self) -> ExperimentDraft:
        """当前草稿（测试和主窗口用）。

        Returns:
            草稿。
        """
        return self._draft

    def is_dirty(self) -> bool:
        """是否有未完成的内容，离开前需要确认。

        Returns:
            已离开第一步、录入过内容，或已创建试验但还没启动时为 True。
        """
        if self._starting:
            return False  # 启动已提交给引擎，离开不丢东西
        return (
            self._created_id is not None or self._step > STEP_TEMPLATE or self._draft.is_modified()
        )

    def confirm_leave(self) -> bool:
        """离开页面前的确认；确认后丢弃向导里的内容。

        Returns:
            可以离开时为 True。
        """
        if not self.is_dirty():
            return True
        confirmed = ConfirmDialog.ask(
            self,
            self.tr("离开新试验向导？"),
            self.tr("向导里已录入的内容还没有提交，离开后会丢失。确定要离开吗？"),
            confirm_text=self.tr("离开"),
            cancel_text=self.tr("留在向导"),
            danger=True,
        )
        if confirmed:
            self.reset()
        return confirmed

    def reset(self) -> None:
        """丢弃向导里的全部内容回到第一步；已创建但没启动的试验一并放弃。"""
        self._discard_created()
        self._draft = ExperimentDraft()
        self._scans.clear()
        self._start_error = None
        self._confirm_error = None
        self._starting = False
        for card in self.reagent_cards.values():
            card.set_scan(None)
        self._load_templates()
        self._sync_samples()
        self._show_step(STEP_TEMPLATE)

    # ================================================================== 步骤 1

    def _load_templates(self) -> None:
        session = self._session
        try:
            self._templates = self._services.templates.list_templates(session)
            error = ""
        except AuthError as exc:
            self._templates = []
            error = describe_auth_error(exc)
        except StorageBusyError:
            self._templates = []
            error = describe_storage_busy()
        for option in self._options:
            self.template_grid.removeWidget(option)
            option.hide()
            option.setParent(None)
            option.deleteLater()
        self._options = []
        current = self._draft.template.template_id if self._draft.template else None
        if current is not None and all(t.template.template_id != current for t in self._templates):
            self._draft = ExperimentDraft()  # 选中的模板已不存在
            current = None
        for index, info in enumerate(self._templates):
            option = TemplateOption(info.template, info.builtin)
            option.chosen.connect(self._on_template_chosen)
            option.set_selected(info.template.template_id == current)
            self._options.append(option)
            self.template_grid.addWidget(option, index // 2, index % 2)
        self.template_empty.setText(error or self.tr("还没有可用的模板"))
        self.template_empty.setVisible(not self._templates)

    @Slot(str)
    def _on_template_chosen(self, template_id: str) -> None:
        info = next((t for t in self._templates if t.template.template_id == template_id), None)
        if info is None:
            return
        current = self._draft.template
        if current is not None and current.template_id == template_id:
            return
        if self._draft.has_sample_input() and not ConfirmDialog.ask(
            self,
            self.tr("更换模板？"),
            self.tr("更换模板会清空已录入的样品和已选的管位。确定要更换吗？"),
            confirm_text=self.tr("更换"),
            cancel_text=self.tr("保留当前模板"),
        ):
            return
        self._draft.choose_template(info.template)
        for option in self._options:
            option.set_selected(option.template.template_id == template_id)
        self._sync_lambda()
        self._render()

    # ================================================================== 步骤 2

    @Slot(str, str)
    def _on_scan_requested(self, kind_value: str, raw: str) -> None:
        kind = ReagentKind(kind_value)
        card = self.reagent_cards[kind]
        try:
            scan = self._services.reagents.scan(self._session, raw, expected_kind=kind)
        except AuthError as exc:
            self._show_banner_error(describe_auth_error(exc))
            return
        except StorageBusyError:
            self._show_banner_error(describe_storage_busy())
            return
        card.set_scan(scan)
        if scan.kind is kind and scan.batch_no is not None:
            self._scans[kind] = scan
        else:
            self._scans.pop(kind, None)  # 种类放错、读不出码：不算这一种的批次
        if scan.valid:
            self._draft.reagent_codes[kind] = scan.raw
        else:
            self._draft.reagent_codes.pop(kind, None)
        self._sync_lambda()
        self._render()
        if scan.valid:
            self._focus_next_empty(kind)
        else:
            card.focus_input()
        if scan.locks:
            self._popup_lock({kind: scan})

    def _popup_lock(self, locked: Mapping[ReagentKind, ReagentScan]) -> None:
        """试剂锁定流程时弹窗提示（不阻塞），页面上的红色横幅同时保留。"""
        lines = []
        for kind, scan in locked.items():
            batch = f" {scan.batch_no}" if scan.batch_no else ""
            lines.append(f"{reagent_kind_label(kind)}{batch}：{describe_scan(scan, kind)}")
        message = (
            "\n".join(lines)
            + "\n\n"
            + self.tr(
                "试验流程已锁定，不能进入下一步。请更换合格的试剂批次后重新扫码。本次校验已记入审计。"
            )
        )
        if self._lock_dialog is not None:
            self._lock_dialog.close()
        dialog = NoticeDialog(self.tr("试剂未通过校验"), message, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(self._forget_lock_dialog)
        self._lock_dialog = dialog
        dialog.open()

    def _forget_lock_dialog(self) -> None:
        self._lock_dialog = None

    @property
    def lock_dialog(self) -> NoticeDialog | None:
        """当前打开的试剂锁定提示，没有时为 None。"""
        return self._lock_dialog

    def _sync_lambda(self) -> None:
        """把鲎试剂的 λ 交给草稿，并刷新样品表里按 λ 算出的外加内毒素。"""
        lal = self._scans.get(ReagentKind.LAL_REAGENT)
        value = lal.labeled_sensitivity if lal is not None and lal.valid else None
        self._draft.set_labeled_sensitivity(value)
        self._sync_samples()

    def reagents_ready(self) -> bool:
        """试剂是否满足进入下一步的条件：全部通过校验，且模板需要的 λ 已知。

        Returns:
            可以继续时为 True。
        """
        return self.reagents_ok() and not self._draft.lambda_missing

    def _show_banner_error(self, text: str) -> None:
        self.lock_banner.show_message("warning", "triangle-alert", text)

    def _focus_next_empty(self, after: ReagentKind) -> None:
        kinds = list(ReagentKind)
        for kind in kinds[kinds.index(after) + 1 :] + kinds[: kinds.index(after)]:
            scan = self._scans.get(kind)
            if scan is None or not scan.valid:
                self.reagent_cards[kind].focus_input()
                return

    def reagents_ok(self) -> bool:
        """三种试剂是否都已通过校验。

        Returns:
            全部通过时为 True。
        """
        return all(
            (scan := self._scans.get(kind)) is not None and scan.valid for kind in ReagentKind
        )

    def locked_scans(self) -> dict[ReagentKind, ReagentScan]:
        """会锁定流程的校验结果。

        Returns:
            不合格的种类及其结果。
        """
        return {kind: scan for kind, scan in self._scans.items() if scan.locks}

    def is_locked(self) -> bool:
        """流程是否被试剂锁定。

        Returns:
            有任何一种试剂过期、未备案或无法确认时为 True。
        """
        return bool(self.locked_scans())

    def _render_lock(self) -> None:
        locked = self.locked_scans()
        if not locked:
            if self.reagents_ok() and self._draft.lambda_missing:
                self.lock_banner.show_message(
                    "danger",
                    "lock",
                    self.tr("缺少标示灵敏度 λ，不能进入下一步"),
                    self.tr(
                        "这个模板按 λ 计算外加内毒素。请换一个有标示灵敏度的鲎试剂批次，"
                        "或把灵敏度补录到备案信息后重新扫码。"
                    ),
                )
            else:
                self.lock_banner.clear()
            return
        lines = []
        for kind, scan in locked.items():
            batch = f" {scan.batch_no}" if scan.batch_no else ""
            lines.append(f"{reagent_kind_label(kind)}{batch}：{describe_scan(scan, kind)}")
        self.lock_banner.show_message(
            "danger",
            "lock",
            self.tr("试剂未通过校验，流程已锁定，不能进入下一步"),
            "\n".join(lines)
            + "\n"
            + self.tr("请更换合格的试剂批次后重新扫码。本次校验已记入审计。"),
        )

    # ================================================================== 步骤 3

    def _sync_samples(self) -> None:
        """按草稿重建样品表并同步孔板。"""
        self._syncing = True
        try:
            rows = self._draft.rows
            self.sample_table.set_rows(
                [
                    [
                        str(i),
                        row.name,
                        row.batch_no,
                        _format_number(row.spiked_endotoxin),
                        self._tube_text(i - 1),
                    ]
                    for i, row in enumerate(rows, start=1)
                ]
            )
            for i, row in enumerate(rows):
                if row.lambda_multiple is not None:  # 由 λ 算出，不能手工改
                    item = self.sample_table.cell_item(i, COL_SPIKE)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.plate.set_selected(self._draft.selected)
            for edit, text in (
                (self.mvd_edit, self._draft.mvd_text),
                (self.limit_edit, self._draft.limit_text),
            ):
                if edit.text() != text:
                    edit.setText(text)
        finally:
            self._syncing = False

    def _on_limits_edited(self) -> None:
        if self._syncing:
            return
        self._draft.mvd_text = self.mvd_edit.text()
        self._draft.limit_text = self.limit_edit.text()
        self._render()

    def _tube_text(self, index: int) -> str:
        tube = self._draft.tube_of_row(index)
        return "–" if tube is None else str(tube)

    @Slot(int, int)
    def _on_cell_edited(self, row: int, col: int) -> None:
        if self._syncing or not 0 <= row < len(self._draft.rows):
            return
        sample = self._draft.rows[row]
        text = self.sample_table.cell_text(row, col)
        if col == COL_SPIKE and sample.lambda_multiple is not None:
            return
        if col == COL_NAME:
            sample.name = text
        elif col == COL_BATCH:
            sample.batch_no = text
        elif col == COL_SPIKE:
            sample.spiked_endotoxin = _parse_number(text)
        self._render()

    @Slot(list)
    def _on_selection_changed(self, tubes: list[int]) -> None:
        if self._syncing:
            return
        self._draft.set_selected(tubes)
        for i in range(self._draft.expected_count):
            self.sample_table.set_cell_text(i, COL_TUBE, self._tube_text(i))
        self._render()

    def _auto_select(self) -> None:
        self.plate.set_selected(self._draft.auto_select())

    def _render_samples(self) -> None:
        draft = self._draft
        template = draft.template
        scans = self._scans
        if template is not None:
            lal = scans.get(ReagentKind.LAL_REAGENT)
            sensitivity = (
                self.tr(" · 标示灵敏度 {value:g} EU/mL").format(value=lal.labeled_sensitivity)
                if lal is not None and lal.labeled_sensitivity is not None
                else ""
            )
            self.summary_line.setText(
                f"{builtin_name_text(template.name)}"
                + self.tr(" · {count} 管").format(count=template.tube_count)
                + sensitivity
            )
        else:
            self.summary_line.setText("–")
        for kind, tile in self.tiles.items():
            tile.set_scan(scans.get(kind))
        total = draft.expected_count
        picked = draft.selected_count
        self.count_label.setText(
            self.tr("已选 {picked} / {total} ·").format(picked=picked, total=total)
        )
        diff = draft.difference
        if total == 0:
            text, role = self.tr("请先选择模板"), "warn"
        elif picked == 0:
            text, role = self.tr("请在孔板上选择管位"), "warn"
        elif diff == 0:
            text, role = self.tr("管位已对应"), "ok"
        elif diff < 0:
            text, role = self.tr("还差 {n} 个").format(n=-diff), "warn"
        else:
            text, role = self.tr("多选了 {n} 个").format(n=diff), "warn"
        self.match_label.setText(text)
        self.match_label.set_role(role)
        empty = draft.sample_problems()
        if empty:
            self.sample_problem.setText(
                self.tr("第 {rows} 行的样品名称为空，请填写").format(rows=join_items(empty))
            )
        self.sample_problem.setVisible(bool(empty))
        bad_limits = draft.limit_problems()
        names = {"mvd": self.tr("MVD"), "endotoxin_limit": self.tr("内毒素限值")}
        self.limit_problem.setText(
            self.tr("“{names}”必须是大于 0 的数字，或者留空").format(
                names=self.tr("、").join(names[f] for f in bad_limits)
            )
            if bad_limits
            else ""
        )
        self.limit_problem.setVisible(bool(bad_limits))
        self.sample_next.setEnabled(draft.tubes_match and not empty and not bad_limits)
        if not self.sample_next.isEnabled():
            self.sample_next.setToolTip(self.tr("管位数量要与模板一致，样品名称不能为空"))
        else:
            self.sample_next.setToolTip("")

    # ================================================================== 步骤 4

    def _render_confirm(self) -> None:
        draft = self._draft
        template = draft.template
        session = self._session
        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if template is None or session is None:
            self.summary_table.set_rows([])
            self._render_start_state()
            return
        rows_used = sorted({tube_to_position(t).row_index for t in draft.selected})
        if rows_used:
            span = (
                row_label(rows_used[0])
                if len(rows_used) == 1
                else f"{row_label(rows_used[0])}–{row_label(rows_used[-1])}"
            )
        else:
            span = "–"
        user = session.user
        facts: list[tuple[str, str, bool]] = [
            (self.tr("模板"), builtin_name_text(template.name), False),
            (self.tr("试验类型"), experiment_type_label(template.experiment_type), False),
            (
                self.tr("试管"),
                self.tr("{count} 管 · {rows}").format(count=draft.selected_count, rows=span),
                False,
            ),
            (
                self.tr("孵育时长"),
                self.tr("{minutes:g} 分钟").format(
                    minutes=self._services.engine.config.incubation_minutes
                ),
                False,
            ),
            (self.tr("操作人"), f"{user.display_name} · {role_label(user.role)}", False),
        ]
        if draft.mvd is not None:
            facts.append((self.tr("MVD"), f"{draft.mvd:g}", True))
        if draft.endotoxin_limit is not None:
            facts.append((self.tr("内毒素限值"), f"{draft.endotoxin_limit:g} EU/mL", True))
        for kind in ReagentKind:
            scan = self._scans.get(kind)
            if scan is not None and scan.batch_no:
                until = scan.expiry_date.isoformat() if scan.expiry_date else "–"
                facts.append(
                    (
                        reagent_kind_label(kind),
                        self.tr("{batch} · 效期至 {date}").format(batch=scan.batch_no, date=until),
                        True,
                    )
                )
        for row_no, (label, value, mono) in enumerate(facts):
            self.info_grid.addWidget(Label(label, "lab"), row_no, 0, Qt.AlignmentFlag.AlignTop)
            value_label = Label(value, "body", mono=mono)
            value_label.setWordWrap(True)
            self.info_grid.addWidget(value_label, row_no, 1)
        self.summary_table.set_rows(
            [
                {
                    "i": i,
                    "name": row.name,
                    "batch": row.batch_no,
                    "spike": _format_number(row.spiked_endotoxin),
                    "tube": tube if (tube := draft.tube_of_row(i - 1)) is not None else "",
                    "row": (
                        row_label(tube_to_position(tube).row_index)
                        if (tube := draft.tube_of_row(i - 1)) is not None
                        else ""
                    ),
                }
                for i, row in enumerate(draft.rows, start=1)
            ]
        )
        self._render_start_state()

    def can_start(self) -> bool:
        """当前会话是否有创建和启动试验的权限。

        Returns:
            两项权限都有时为 True。
        """
        session = self._session
        return (
            session is not None
            and session.can(Permission.EXPERIMENT_CREATE)
            and session.can(Permission.EXPERIMENT_RUN)
        )

    def _render_start_state(self) -> None:
        if self._starting:
            self.start_banner.show_message(
                "info",
                "info",
                self.tr("正在启动"),
                self.tr("正在连接设备、检查温度和视频，请稍候……"),
            )
        elif self._confirm_error is not None:
            self.start_banner.show_message(
                "danger", "triangle-alert", self.tr("无法启动"), self._confirm_error()
            )
        elif self._start_error is not None:
            self.start_banner.show_message(
                "danger",
                "triangle-alert",
                self.tr("启动失败，试验仍为就绪，可以重试"),
                self.tr("原因：") + self._start_error,
            )
        else:
            self.start_banner.clear()
        retry = self._start_error is not None and self._created_id is not None
        self.confirm_start.setText(self.tr("重试启动") if retry else self.tr("确认并启动"))
        allowed = self.can_start()
        self.confirm_start.setEnabled(allowed and not self._starting)
        self.confirm_start.setToolTip("" if allowed else self.tr("当前角色无此权限"))
        self.confirm_back.setEnabled(not self._starting)

    def _back_from_confirm(self) -> None:
        # 回去修改会让已创建的试验过时：放弃它，下次确认时按新内容重新创建。
        self._discard_created()
        self._start_error = None
        self._confirm_error = None
        self._go(STEP_SAMPLES)

    def _discard_created(self) -> None:
        created, self._created_id = self._created_id, None
        if created is None or self._starting:
            return
        try:
            self._services.experiments.abandon_experiment(self._session, created)
        except (AuthError, ServiceError, StorageBusyError):
            _log.warning("放弃未启动的试验 %s 失败，它会保持就绪状态", created, exc_info=True)

    def _confirm(self) -> None:
        session = self._session
        self._confirm_error = None
        self._start_error = None
        try:
            if self._created_id is None:
                self._created_id = self._services.experiments.create_experiment(
                    session, self._draft.to_request()
                )
            self._services.experiments.start_experiment(session, self._created_id)
        except ReagentLockedError as exc:
            self._on_locked_at_service(exc)
            return
        except ServiceError as exc:
            self._confirm_error = lambda exc=exc: describe_service_error(exc)
            self._render()
            return
        except AuthError as exc:
            self._confirm_error = lambda exc=exc: describe_auth_error(exc)
            self._render()
            return
        except StorageBusyError:
            self._confirm_error = describe_storage_busy
            self._render()
            return
        self._starting = True
        self._render()

    def _on_locked_at_service(self, error: ReagentLockedError) -> None:
        """服务层复核发现试剂不合格（例如隔夜过期）：回到“校验试剂”并锁定。"""
        self._discard_created()
        for kind, scan in error.scans.items():
            if scan.valid:
                continue
            self._scans[kind] = scan
            self._draft.reagent_codes.pop(kind, None)
            self.reagent_cards[kind].set_scan(scan)
        self._go(STEP_REAGENTS)
        self.lock_banner.show_message(
            "danger",
            "lock",
            self.tr("试剂未通过校验，流程已锁定，不能进入下一步"),
            "\n".join(describe_lock_reasons(error)),
        )
        self._popup_lock({k: v for k, v in error.scans.items() if not v.valid})

    # ================================================================== 引擎事件

    @Slot(object)
    def _on_state_changed(self, event: StateChanged) -> None:
        if (
            self._starting
            and event.experiment_id == self._created_id
            and event.current is ExperimentState.INCUBATING
        ):
            experiment_id = event.experiment_id
            self._starting = False
            self._created_id = None
            self.reset()
            self.started.emit(experiment_id)

    @Slot(object)
    def _on_alert(self, alert: EngineAlert) -> None:
        if (
            self._starting
            and alert.experiment_id == self._created_id
            and alert.code in (AlertCode.START_FAILED, AlertCode.START_CANCELLED)
        ):
            self._starting = False
            self._start_error = alert.message
            self._render()

    # ================================================================== 步骤切换

    def _go(self, step: int) -> None:
        """切换步骤；前进要满足条件，后退不丢数据。"""
        if step > self._step:
            if step >= STEP_REAGENTS and self._draft.template is None:
                return
            if step >= STEP_SAMPLES and not self.reagents_ready():
                return
            if step >= STEP_CONFIRM and not (
                self._draft.tubes_match
                and not self._draft.sample_problems()
                and not self._draft.limit_problems()
            ):
                return
        self._show_step(step)

    def _show_step(self, step: int) -> None:
        self._step = step
        # 栈的高度只取当前步骤的，否则最高的第 3 步会让其他步骤也出现滚动条。
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
            if page is not None:
                policy = (
                    QSizePolicy.Policy.Preferred if index == step else QSizePolicy.Policy.Ignored
                )
                page.setSizePolicy(policy, policy)
        self.stack.setCurrentIndex(step)
        self.steps.set_current(step)
        self._render()
        if step == STEP_REAGENTS:
            for kind in ReagentKind:
                scan = self._scans.get(kind)
                if scan is None or not scan.valid:
                    self.reagent_cards[kind].focus_input()
                    break

    def _render(self) -> None:
        template_chosen = self._draft.template is not None
        self.template_next.setEnabled(template_chosen)
        self._render_lock()
        self.reagent_next.setEnabled(self.reagents_ready())
        self.reagent_next.setToolTip(
            self.tr("这个模板需要鲎试剂的标示灵敏度 λ") if self._draft.lambda_missing else ""
        )
        scan_allowed = self._session is not None and self._session.can(Permission.REAGENT_MANAGE)
        for card in self.reagent_cards.values():
            card.set_scan_enabled(scan_allowed)
        self._render_samples()
        self._render_confirm()
