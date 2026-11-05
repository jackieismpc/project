"""试剂溯源页：已录入的批次、校验记录、扫码查询，以及政府溯源码入口（接入预留）。

所有角色可以查看和查询（查询只校验、不写记录）。批次和校验记录来自新试验向导里的扫码；
过期的批次整行高亮并写明「已过期」，剩余天数不超过 30 天的提示「即将到期」。
政府溯源平台按 ``interfaces/traceability.py`` 预留入口，目前没有真实实现。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from gelinspect.domain.entities import ReagentBatch
from gelinspect.domain.enums import ReagentCheckStatus
from gelinspect.interfaces.storage import (
    ReagentBatchEntry,
    ReagentCheckRecord,
    StorageBusyError,
)
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.reagent_service import EXPIRING_SOON_DAYS
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_storage_busy,
    reagent_kind_label,
)
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.reagent_result import ReagentResultView


def _local(value: object) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if hasattr(value, "astimezone") else ""


class ReagentsPage(QWidget):
    """试剂溯源页。"""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._batches: list[ReagentBatchEntry] = []
        self._checks: list[ReagentCheckRecord] = []
        self._error = ""

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

        self.title = Label("", "h1")
        column.addWidget(self.title)

        self.query_card = Card(" ")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.query_input = QLineEdit()
        self.query_input.setMinimumHeight(44)
        self.query_input.returnPressed.connect(self.run_query)
        self.query_button = Button("", ButtonVariant.PRIMARY, icon="search")
        self.query_button.clicked.connect(self.run_query)
        row.addWidget(self.query_input, 1)
        row.addWidget(self.query_button)
        self.query_card.body.addLayout(row)
        self.query_hint = Label("", "meta")
        self.query_hint.setWordWrap(True)
        self.query_card.body.addWidget(self.query_hint)
        self.query_error = Label("", "warn")
        self.query_error.setWordWrap(True)
        self.query_error.setVisible(False)
        self.query_card.body.addWidget(self.query_error)
        self.query_result = ReagentResultView()
        self.query_card.body.addWidget(self.query_result)
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        top_row.addWidget(self.query_card, 3)
        top_row.addWidget(self._build_government_card(), 2)
        column.addLayout(top_row)

        tables = QHBoxLayout()
        tables.setSpacing(14)
        self.batch_card = Card(" ")
        self.batch_table = DataTable(self._batch_columns())
        self.batch_table.setMinimumHeight(200)
        self.batch_card.body.addWidget(self.batch_table)
        tables.addWidget(self.batch_card, 1)
        self.check_card = Card(" ")
        self.check_table = DataTable(self._check_columns())
        self.check_table.setMinimumHeight(200)
        self.check_card.body.addWidget(self.check_table)
        tables.addWidget(self.check_card, 1)
        column.addLayout(tables)

        column.addStretch(1)
        self.retranslate()

    def _build_government_card(self) -> Card:
        """政府溯源码入口（接入预留）：输入框和按钮禁用，只说明状态。"""
        self.gov_card = Card(" ")
        assert self.gov_card.header is not None
        self.gov_badge = StatusBadge("", BadgeTone.NEUTRAL)
        self.gov_card.header.addWidget(self.gov_badge)
        gov_row = QHBoxLayout()
        gov_row.setSpacing(8)
        self.gov_input = QLineEdit()
        self.gov_input.setMinimumHeight(44)
        self.gov_input.setEnabled(False)
        self.gov_button = Button("", ButtonVariant.SECONDARY)
        self.gov_button.setEnabled(False)
        gov_row.addWidget(self.gov_input, 1)
        gov_row.addWidget(self.gov_button)
        self.gov_card.body.addLayout(gov_row)
        self.gov_text = Label("", "meta")
        self.gov_text.setWordWrap(True)
        self.gov_card.body.addWidget(self.gov_text)
        self.gov_card.body.addStretch(1)
        return self.gov_card

    # ------------------------------------------------------------------ 列定义

    def _batch_columns(self) -> list[Column]:
        return [
            Column("kind", self.tr("种类")),
            Column("batch", self.tr("批号"), mono=True),
            Column("expiry", self.tr("效期"), mono=True),
            Column("days", self.tr("剩余天数"), mono=True, align_right=True),
            Column("status", self.tr("状态")),
            Column("last", self.tr("最近校验"), mono=True),
        ]

    def _check_columns(self) -> list[Column]:
        return [
            Column("time", self.tr("时间"), mono=True),
            Column("actor", self.tr("操作者")),
            Column("kind", self.tr("种类")),
            Column("batch", self.tr("批号"), mono=True),
            Column("result", self.tr("结果")),
        ]

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        """重新读取批次和校验记录。"""
        self._error = ""
        self._batches, self._checks = [], []
        try:
            self._batches = self._services.reagents.list_batches(self._session)
            self._checks = list(self._services.reagents.recent_checks(self._session).items)
        except AuthError as error:
            self._error = describe_auth_error(error)
        except StorageBusyError:
            self._error = describe_storage_busy()
        self._render()

    def run_query(self) -> None:
        """按输入框里的试剂码查询（只校验，不写记录）。"""
        raw = self.query_input.text().strip()
        if not raw:
            return
        self.query_error.setVisible(False)
        try:
            scan = self._services.reagents.lookup(self._session, raw)
        except AuthError as error:
            self.query_result.clear()
            self.query_error.setText(describe_auth_error(error))
            self.query_error.setVisible(True)
            return
        self.query_result.set_scan(scan)
        if scan.kind is not None and scan.batch_no:
            self.query_error.setText(
                self.tr("{kind} · 查询结果不写入校验记录").format(
                    kind=reagent_kind_label(scan.kind)
                )
            )
            self.query_error.set_role("meta")
            self.query_error.setVisible(True)

    # ------------------------------------------------------------------ 显示

    def _status(self, entry: ReagentBatchEntry, today: date) -> tuple[str, str | None]:
        """批次的状态文字和高亮色调（过期红、即将到期黄、未备案红）。"""
        batch = ReagentBatch(
            kind=entry.kind,
            batch_no=entry.batch_no,
            expiry_date=entry.expiry_date,
            registered=entry.registered,
            labeled_sensitivity=entry.labeled_sensitivity,
        )
        status = batch.check(today)
        if status is ReagentCheckStatus.EXPIRED:
            return self.tr("已过期"), "danger"
        if status is ReagentCheckStatus.NOT_REGISTERED:
            return self.tr("未备案"), "danger"
        if status is ReagentCheckStatus.EXPIRY_UNKNOWN:
            return self.tr("有效期未知"), "danger"
        days = (entry.expiry_date - today).days if entry.expiry_date else None
        if days is not None and days <= EXPIRING_SOON_DAYS:
            return self.tr("即将到期"), "warning"
        return self.tr("有效"), None

    def _render(self) -> None:
        today = self._services.reagents.today()
        rows = []
        for entry in self._batches:
            text, tone = self._status(entry, today)
            days = (entry.expiry_date - today).days if entry.expiry_date else None
            row: dict[str, object] = {
                "kind": reagent_kind_label(entry.kind),
                "batch": entry.batch_no,
                "expiry": entry.expiry_date.isoformat() if entry.expiry_date else "",
                "days": "" if days is None else days,
                "status": text,
                "last": _local(entry.last_checked_at),
            }
            if tone is not None:
                row["_tone"] = tone
            rows.append(row)
        self.batch_table.set_rows(rows)
        self.batch_table.set_empty_text(
            self._error or self.tr("还没有录入的试剂批次"),
            "" if self._error else self.tr("在新试验向导里扫码校验后，批次会显示在这里"),
        )
        check_rows = []
        for record in self._checks:
            result = {
                ReagentCheckStatus.VALID: self.tr("通过"),
                ReagentCheckStatus.EXPIRED: self.tr("已过期，锁定"),
                ReagentCheckStatus.NOT_REGISTERED: self.tr("未备案，锁定"),
                ReagentCheckStatus.EXPIRY_UNKNOWN: self.tr("有效期未知，锁定"),
                ReagentCheckStatus.MISSING: self.tr("缺少，锁定"),
            }[record.status]
            check_row: dict[str, object] = {
                "time": _local(record.checked_at),
                "actor": record.actor,
                "kind": reagent_kind_label(record.kind),
                "batch": record.batch_no,
                "result": result,
            }
            if record.status is not ReagentCheckStatus.VALID:
                check_row["_tone"] = "danger"
            check_rows.append(check_row)
        self.check_table.set_rows(check_rows)
        self.check_table.set_empty_text(self.tr("还没有校验记录"))
        self._render_government()

    def _render_government(self) -> None:
        status = self._services.reagents.government_status()
        self.gov_badge.set_text(self.tr("接入预留"))
        if status.enabled:
            note = self.tr("政府溯源平台已启用，但本软件尚未实现上报，请等待接口规范。")
        elif status.configured:
            note = self.tr(
                "政府溯源平台未启用。接口按 interfaces/traceability.py 预留，平台规范明确后接入。"
            )
        else:
            note = self.tr(
                "尚未配置政府溯源客户端。接口按 interfaces/traceability.py 预留，"
                "平台规范明确后接入。"
            )
        self.gov_text.setText(note)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("试剂溯源"))
        self.query_card.set_title(self.tr("扫码查询"))
        self.query_input.setPlaceholderText(self.tr("扫码或输入试剂码，按回车查询"))
        self.query_input.setAccessibleName(self.tr("试剂码"))
        self.query_button.setText(self.tr("查询"))
        self.query_hint.setText(
            self.tr("查询只检查备案和效期，不会录入批次。批次在新试验向导里扫码校验后录入。")
        )
        self.batch_card.set_title(self.tr("已录入批次"))
        self.batch_table.set_columns(self._batch_columns())
        self.check_card.set_title(self.tr("校验记录"))
        self.check_table.set_columns(self._check_columns())
        self.gov_card.set_title(self.tr("政府溯源码"))
        self.gov_input.setPlaceholderText(self.tr("政府溯源码（接入预留）"))
        self.gov_input.setAccessibleName(self.tr("政府溯源码"))
        self.gov_button.setText(self.tr("上报"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
