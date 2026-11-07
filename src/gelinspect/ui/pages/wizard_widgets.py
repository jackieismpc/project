"""新试验向导用到的小部件：模板选项、试剂卡片、状态横幅和汇总磁贴。

只负责显示和发出信号，业务判断在 ``services/`` 里。颜色全部来自 QSS 的 token。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import ReagentKind
from gelinspect.domain.templates import ExperimentTemplate
from gelinspect.services.reagent_service import ReagentScan
from gelinspect.ui.labels import (
    builtin_name_text,
    describe_scan,
    experiment_type_label,
    reagent_kind_label,
)
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.reagent_result import ReagentResultView
from gelinspect.ui.widgets.util import repolish


class TemplateOption(QFrame):
    """步骤 1 里的一个模板选项：名称、类型、管数，布局待确认时带徽标。

    Signals:
        chosen: 用户点击或按空格、回车选中，参数为模板标识。
    """

    chosen = Signal(str)

    def __init__(
        self, template: ExperimentTemplate, builtin: bool, parent: QWidget | None = None
    ) -> None:
        """创建选项。

        Args:
            template: 模板。
            builtin: 是否内置。
            parent: 父控件。
        """
        super().__init__(parent)
        self.template = template
        self.builtin = builtin
        self.setProperty("role", "option")
        self.setProperty("selected", False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name = Label(template.name, "val")
        self.name.setWordWrap(True)
        self.badge = StatusBadge("", BadgeTone.WARNING)
        self.badge.setVisible(template.needs_confirmation)
        top.addWidget(self.name, 1)
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self.meta = Label("", "meta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        for child in (self.name, self.meta, self.badge):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新文字。"""
        template = self.template
        self.name.setText(builtin_name_text(template.name))
        source = self.tr("内置") if self.builtin else self.tr("自定义")
        self.meta.setText(
            self.tr("{type} · {count} 管 · {source}").format(
                type=experiment_type_label(template.experiment_type),
                count=template.tube_count,
                source=source,
            )
        )
        self.badge.set_text(self.tr("布局待确认"))
        note = self.tr("，布局待确认") if template.needs_confirmation else ""
        self.setAccessibleName(
            self.tr("模板 {name}，{count} 管").format(
                name=builtin_name_text(template.name), count=template.tube_count
            )
            + note
        )

    def set_selected(self, selected: bool) -> None:
        """切换选中样式。

        Args:
            selected: 是否选中。
        """
        self.setProperty("selected", selected)
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """点击选中。

        Args:
            event: 鼠标事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.chosen.emit(self.template.template_id)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """空格或回车选中。

        Args:
            event: 键盘事件。
        """
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.chosen.emit(self.template.template_id)
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class Banner(QFrame):
    """整行的状态横幅：图标、标题和若干行说明；没有内容时隐藏。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建横幅，初始隐藏。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "banner")
        self.setProperty("tone", "info")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)
        self._icon = Label()
        self._icon.setFixedSize(24, 24)
        outer.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(2)
        self.title = Label("", "h2")
        self.title.setWordWrap(True)
        self.detail = Label("", "body")
        self.detail.setWordWrap(True)
        column.addWidget(self.title)
        column.addWidget(self.detail)
        outer.addLayout(column, 1)
        self.setVisible(False)

    def show_message(self, tone: str, icon: str, title: str, detail: str = "") -> None:
        """显示横幅。

        Args:
            tone: ``danger``、``warning`` 或 ``info``。
            icon: Lucide 图标名。
            title: 标题。
            detail: 说明，多行用换行分隔。
        """
        self.setProperty("tone", tone)
        color = {"danger": "danger", "warning": "warning", "success": "success"}.get(
            tone, "primary"
        )
        self._icon.setPixmap(IconLoader.pixmap(icon, 22, color))
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setAccessibleName(f"{title} {detail}".strip())
        repolish(self)
        self.setVisible(True)

    def clear(self) -> None:
        """隐藏横幅。"""
        self.setVisible(False)


class ReagentCard(QFrame):
    """步骤 2 的一张试剂卡：扫码输入、校验结果和示例码。

    扫码枪是键盘楔式：它把码当作键盘输入并以回车结束，所以输入框的回车就是触发点；
    手输后按回车或点“校验”效果相同。

    Signals:
        scanRequested: 请求校验，参数为试剂种类的值和输入的文字。
    """

    scanRequested = Signal(str, str)  # noqa: N815

    def __init__(self, kind: ReagentKind, parent: QWidget | None = None) -> None:
        """创建卡片。

        Args:
            kind: 这张卡对应的试剂种类。
            parent: 父控件。
        """
        super().__init__(parent)
        self.kind = kind
        self.setProperty("role", "reagent")
        self.setProperty("state", "idle")
        self._scan: ReagentScan | None = None
        self._samples: list[tuple[str, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = Label("", "h2")
        self.badge = StatusBadge("", BadgeTone.NEUTRAL)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.badge)
        layout.addLayout(header)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setMinimumHeight(44)
        self.input.setClearButtonEnabled(False)
        self.input.returnPressed.connect(self._emit_scan)
        self.scan_button = Button("", ButtonVariant.SECONDARY)
        self.scan_button.clicked.connect(self._emit_scan)
        row.addWidget(self.input, 1)
        row.addWidget(self.scan_button)
        layout.addLayout(row)
        self.message = Label("", "warn")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)
        self.result = ReagentResultView()
        layout.addWidget(self.result)
        self.samples_title = Label("", "lab")
        layout.addWidget(self.samples_title)
        self.samples_box = QVBoxLayout()
        self.samples_box.setSpacing(0)
        layout.addLayout(self.samples_box)
        layout.addStretch(1)
        self.retranslate()

    # ------------------------------------------------------------------ 公共接口

    def scan(self) -> ReagentScan | None:
        """当前显示的校验结果。

        Returns:
            结果，尚未校验时为 None。
        """
        return self._scan

    def set_scan(self, scan: ReagentScan | None) -> None:
        """显示校验结果并更新卡片状态。

        Args:
            scan: 校验结果；None 表示清空。
        """
        self._scan = scan
        if scan is None:
            self.result.clear()
            self.message.setVisible(False)
        else:
            self.input.setText(scan.raw)
            if scan.batch_no is not None and scan.kind is self.kind:
                self.result.set_scan(scan, self.kind)
                self.message.setVisible(False)
            else:  # 读不出码、种类放错：只提示，不当作这张卡的批次
                self.result.clear()
                self.message.setText(describe_scan(scan, self.kind))
                self.message.setVisible(True)
        self._refresh_state()

    def set_samples(self, samples: list[tuple[str, str]]) -> None:
        """设置示例码（仅模拟配置有）。

        Args:
            samples: ``(显示名称, 扫码原文)``。
        """
        self._samples = list(samples)
        while self.samples_box.count():
            item = self.samples_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for label, raw in self._samples:
            button = Button(label, ButtonVariant.SECONDARY)
            button.setProperty("role", "link")
            button.setMinimumHeight(32)
            button.setToolTip(self.tr("点击填入并校验这个示例码"))
            button.clicked.connect(lambda _=False, text=raw: self._use_sample(text))
            self.samples_box.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self.samples_title.setVisible(bool(self._samples))

    def focus_input(self) -> None:
        """把光标放到输入框，方便直接扫码。"""
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)
        self.input.selectAll()

    def set_scan_enabled(self, enabled: bool) -> None:
        """启用或禁用扫码输入（没有权限时禁用）。

        Args:
            enabled: 是否可用。
        """
        for widget in (self.input, self.scan_button):
            widget.setEnabled(enabled)
        self.input.setToolTip("" if enabled else self.tr("当前角色无此权限"))

    # ------------------------------------------------------------------ 内部

    def _emit_scan(self) -> None:
        text = self.input.text().strip()
        if text:
            self.scanRequested.emit(self.kind.value, text)

    def _use_sample(self, raw: str) -> None:
        self.input.setText(raw)
        self._emit_scan()

    def _refresh_state(self) -> None:
        scan = self._scan
        if scan is not None and scan.valid:
            state, tone, text = "ok", BadgeTone.SUCCESS, self.tr("已通过")
        elif scan is not None and scan.locks:
            state, tone, text = "bad", BadgeTone.DANGER, self.tr("已锁定")
        elif scan is not None:
            state, tone, text = "idle", BadgeTone.WARNING, self.tr("请重新扫码")
        else:
            state, tone, text = "idle", BadgeTone.NEUTRAL, self.tr("待扫码")
        self.setProperty("state", state)
        self.badge.set_tone(tone)
        self.badge.set_text(text)
        repolish(self)

    def retranslate(self) -> None:
        """按当前语言刷新文字。"""
        name = reagent_kind_label(self.kind)
        self.title.setText(name)
        self.input.setPlaceholderText(self.tr("扫码或输入试剂码，按回车确认"))
        self.input.setAccessibleName(self.tr("{name}试剂码").format(name=name))
        self.scan_button.setText(self.tr("校验"))
        self.scan_button.setAccessibleName(self.tr("校验{name}").format(name=name))
        self.samples_title.setText(self.tr("示例码"))
        self.setAccessibleName(name)
        if self._scan is not None and self.message.isVisible():
            self.message.setText(describe_scan(self._scan, self.kind))
        self._refresh_state()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class ReagentTile(QFrame):
    """步骤 3 汇总里的一块试剂信息：种类、批号、备案和效期（设计稿的 ``.rc``）。"""

    def __init__(self, kind: ReagentKind, parent: QWidget | None = None) -> None:
        """创建磁贴。

        Args:
            kind: 试剂种类。
            parent: 父控件。
        """
        super().__init__(parent)
        self.kind = kind
        self._scan: ReagentScan | None = None
        self.setProperty("role", "tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self.title = Label("", "lab")
        self.batch = Label("", "body", mono=True)
        self.status = Label("", "ok")
        self.status.setWordWrap(True)
        for label in (self.title, self.batch, self.status):
            layout.addWidget(label)
        layout.addStretch(1)
        self.retranslate()

    def set_scan(self, scan: ReagentScan | None) -> None:
        """显示校验结果。

        Args:
            scan: 校验结果；None 表示未校验。
        """
        self._scan = scan
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新文字。"""
        self.title.setText(reagent_kind_label(self.kind))
        scan = self._scan
        if scan is None:
            self.batch.setText("–")
            self.status.setText(self.tr("未校验"))
            self.status.set_role("warn")
            return
        self.batch.setText(scan.batch_no or "–")
        self.status.setText(describe_scan(scan, self.kind))
        self.status.set_role("ok" if scan.valid else "danger")

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
