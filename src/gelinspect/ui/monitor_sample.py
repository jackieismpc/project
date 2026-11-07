"""试验监控页小样：用真实组件按 ``docs/design/Monitor.dc.html`` 排版。

仅用于组件展示与视觉对照，不连接任何业务数据。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import permissions_of
from gelinspect.ui.theme.fonts import load_fonts
from gelinspect.ui.widgets.app_shell import AppShell, NavMode, ShellSettings
from gelinspect.ui.widgets.badge import StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateLegend, TubeStrip, plate_title
from gelinspect.ui.widgets.quick_toolbar import QuickToolbar
from gelinspect.ui.widgets.tabs import Tabs


def mono_span(text: str) -> str:
    """生成等宽字体的富文本片段。

    Args:
        text: 文字。

    Returns:
        可嵌入 ``QLabel`` 富文本的 HTML。
    """
    return f"<span style=\"font-family:'{load_fonts().mono}'\">{text}</span>"


def monitor_states() -> dict[int, WellState]:
    """设计稿示例的孔状态：1–6 阳性，7 待复核，8 阴性，9–16 读数中，17 孵育中。

    Returns:
        管号到状态的映射。
    """
    states: dict[int, WellState] = {}
    for n in range(1, 18):
        if n <= 6:
            states[n] = WellState.POSITIVE
        elif n == 7:
            states[n] = WellState.REVIEW
        elif n == 8:
            states[n] = WellState.NEGATIVE
        elif n <= 16:
            states[n] = WellState.READING
        else:
            states[n] = WellState.INCUBATING
    return states


def configure_demo_toolbar(toolbar: QuickToolbar) -> None:
    """给快捷栏填入演示用的状态：仪器已连接、简体中文、实验员张工及其权限。

    Args:
        toolbar: 要配置的快捷栏。
    """
    toolbar.set_instrument_status(True)
    toolbar.set_language("简体中文")
    toolbar.set_user("张工", "实验员")
    toolbar.set_user_permissions(permissions_of(Role.EXPERIMENTER))


class MonitorPage(QWidget):
    """试验监控页内容：标题行、信息卡、标签、孔板、视频卡和数据表（可滚动）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建页面内容。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
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

        col = QVBoxLayout(page)
        col.setContentsMargins(24, 20, 24, 28)
        col.setSpacing(14)
        col.addLayout(self._top_row())
        col.addWidget(self._facts_card())
        tabs = Tabs(["试验数据记录", "图像显示", "报告打印预览", "单批次报告"])
        col.addWidget(tabs)
        work = QHBoxLayout()
        work.setSpacing(14)
        work.addWidget(self._plate_card(), 2)
        work.addWidget(self._video_card(), 1)
        col.addLayout(work)
        col.addWidget(self._table_card())
        col.addStretch(1)

    def _top_row(self) -> QHBoxLayout:
        """顶部一行：标题、FileId、状态、温度、停止按钮。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(Label("灵敏度复核 (2λ:1)", "h1"))
        row.addWidget(Label("FileId 65", "meta", mono=True))
        row.addWidget(StatusBadge("第 2 排读数中"))
        row.addStretch(1)
        temp = Label("37.1 ℃", "readout", mono=True)
        temp.setAccessibleName("检测室温度 37.1 摄氏度")
        row.addWidget(temp)
        row.addWidget(Button("停止试验", ButtonVariant.DANGER))
        return row

    def _facts_card(self) -> Card:
        """试验信息卡：批号、灵敏度、操作人、开始时间。

        Returns:
            卡片。
        """
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(20)
        facts = [
            ("鲎试剂批号", "2605-01", True),
            ("内毒素批号", "E2604-3", True),
            ("检查用水批号", "W2610-11", True),
            ("标示灵敏度 λ", "0.5 EU/ml", True),
            ("实测灵敏度 λc", "待完成", True),
            ("操作人", "张工", False),
            ("开始时间", "13:49:42", True),
        ]
        for label, value, mono in facts:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(Label(label, "lab"))
            cell.addWidget(Label(value, "val", mono=mono))
            row.addLayout(cell, 1)
        card.body.addLayout(row)
        return card

    def _plate_card(self) -> Card:
        """孔板卡片：标题、图例与 PlateGrid。

        Returns:
            卡片。
        """
        card = Card(plate_title())
        assert card.header is not None
        card.header.addWidget(PlateLegend())
        self.plate = PlateGrid()
        self.plate.set_states(monitor_states())
        self.plate.set_countdown(1, "已读数")
        self.plate.set_countdown(2, "读数中", active=True)
        self.plate.set_countdown(3, "12:48")
        card.body.addWidget(self.plate)
        return card

    def _video_card(self) -> Card:
        """实时视频卡片：试管条、算法信息与回放按钮。

        Returns:
            卡片。
        """
        card = Card("实时视频")
        assert card.header is not None
        card.header.addWidget(Label("翻转录制中", "meta"))
        card.body.addWidget(
            TubeStrip(
                [0.62, 0.62, 0.60, 0.62, 0.61, 0.62, 0.60, 0.61],
                caption_left="Row02 · 翻转后",
                caption_right="4000×3000 · 15 fps",
            )
        )
        info = Label(f"算法判读 {mono_span('1.4 s')} · 翻转角度 {mono_span('180.2°')}", "meta")
        info.setTextFormat(Qt.TextFormat.RichText)
        card.body.addWidget(info)
        replay = Button("回放第 1 排")
        card.body.addWidget(replay)
        card.body.addStretch(1)
        return card

    def _table_card(self) -> Card:
        """试验数据记录表。

        Returns:
            卡片。
        """
        card = Card("试验数据记录")
        table = DataTable(
            [
                Column("tube", "试管编号", mono=True),
                Column("sample", "样品名称"),
                Column("serial", "序列号", mono=True),
                Column("lot", "批号", mono=True),
                Column("dilution", "稀释倍数", mono=True),
                Column("spike", "外加内毒素", mono=True),
                Column("result", "阴阳性", kind="state"),
            ],
            fit_content=True,
        )
        states = monitor_states()
        spikes = [1, 1, 1, 1, 0.5, 0.5, 0.5, 0.5]
        table.set_rows(
            [
                {
                    "tube": n,
                    "sample": "灵敏度复核",
                    "serial": "",
                    "lot": "",
                    "dilution": "",
                    "spike": spikes[n - 1],
                    "result": states[n],
                }
                for n in range(1, 9)
            ]
        )
        card.body.addWidget(table)
        return card


class MonitorSample(QWidget):
    """试验监控页小样：AppShell（快捷栏、侧栏）加 :class:`MonitorPage`。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: ShellSettings | None = None,
        mode: NavMode | None = None,
        animations_enabled: bool = True,
    ) -> None:
        """创建小样。

        Args:
            parent: 父控件。
            settings: 侧栏模式持久化接口，默认 ``QSettings``。
            mode: 强制初始侧栏模式（截图用）。
            animations_enabled: 是否启用侧栏动画。
        """
        super().__init__(parent)
        self.setProperty("role", "root")
        self.setWindowTitle("试验监控 - 小样")
        self.page = MonitorPage()
        self.shell = AppShell(
            self.page, settings=settings, mode=mode, animations_enabled=animations_enabled
        )
        self.shell.nav.set_current("monitor")
        configure_demo_toolbar(self.shell.toolbar)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.shell)
        self.nav = self.shell.nav
        self.scroll_area = self.page.scroll_area
        self.plate = self.page.plate
