"""试剂校验结果的展示块：批号、结论、剩余天数和标示灵敏度。

向导的试剂卡片、试剂溯源页的查询结果和锁定说明共用。结论用文字加颜色表达，不只靠颜色。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from gelinspect.domain.enums import ReagentKind
from gelinspect.services.reagent_service import EXPIRING_SOON_DAYS, ReagentScan, ScanOutcome
from gelinspect.ui.labels import describe_days_remaining, describe_scan
from gelinspect.ui.widgets.label import Label

_LOCKING_OR_INVALID = (
    ScanOutcome.EXPIRED,
    ScanOutcome.NOT_REGISTERED,
    ScanOutcome.EXPIRY_UNKNOWN,
    ScanOutcome.REGISTRY_UNAVAILABLE,
    ScanOutcome.NOT_CONFIGURED,
)


class ReagentResultView(QWidget):
    """一次校验结果的展示块。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建展示块，初始为空。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._scan: ReagentScan | None = None
        self._expected: ReagentKind | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.batch = Label("", "val", mono=True)
        self.status = Label("", "ok")
        self.status.setWordWrap(True)
        self.days = Label("", "meta")
        self.days.setWordWrap(True)
        self.extra = Label("", "meta")
        self.extra.setWordWrap(True)
        for label in (self.batch, self.status, self.days, self.extra):
            layout.addWidget(label)
        self.clear()

    def scan(self) -> ReagentScan | None:
        """当前展示的结果。

        Returns:
            结果，空时为 None。
        """
        return self._scan

    def clear(self) -> None:
        """清空。"""
        self._scan = None
        for label in (self.batch, self.status, self.days, self.extra):
            label.setText("")
            label.setVisible(False)

    def set_scan(self, scan: ReagentScan, expected: ReagentKind | None = None) -> None:
        """显示一次校验结果。

        Args:
            scan: 校验结果。
            expected: 期望的试剂种类，种类放错时用于说明。
        """
        self._scan = scan
        self._expected = expected
        self._render()

    def _render(self) -> None:
        scan = self._scan
        if scan is None:
            return
        self.batch.setText(scan.batch_no or "")
        self.batch.setVisible(bool(scan.batch_no))
        self.status.setText(describe_scan(scan, self._expected))
        self.status.set_role(
            "ok" if scan.valid else ("danger" if scan.outcome in _LOCKING_OR_INVALID else "warn")
        )
        self.status.setVisible(True)
        days = describe_days_remaining(scan.days_remaining) if scan.expiry_date else ""
        if scan.valid and scan.days_remaining is not None:
            if scan.days_remaining <= EXPIRING_SOON_DAYS:
                days += self.tr("（即将到期）")
                self.days.set_role("warn")
            else:
                self.days.set_role("meta")
        else:
            self.days.set_role("meta")
        self.days.setText(days)
        self.days.setVisible(bool(days))
        extra = ""
        if scan.labeled_sensitivity is not None:
            extra = self.tr("标示灵敏度 {value:g} EU/mL").format(value=scan.labeled_sensitivity)
        self.extra.setText(extra)
        self.extra.setVisible(bool(extra))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时重新生成文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._render()
