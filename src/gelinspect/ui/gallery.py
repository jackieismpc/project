"""组件展示页：``uv run python -m gelinspect.ui.gallery``。

展示设计系统的全部组件与状态，另含一个按设计稿排版的试验监控小样。
离屏截图::

    QT_QPA_PLATFORM=offscreen uv run python -m gelinspect.ui.gallery --screenshot out.png
    可选参数：--size 1280x800、--page gallery|monitor、--full、--scale 2、
    --nav-mode auto|pinned（侧栏模式）、--nav-open（自动隐藏模式下展开侧栏）
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.plate import HOLES_PER_ROW, TUBE_COUNT
from gelinspect.ui.monitor_sample import (
    MonitorPage,
    MonitorSample,
    configure_demo_toolbar,
    monitor_states,
)
from gelinspect.ui.theme.fonts import FontReport, last_report
from gelinspect.ui.theme.stylesheet import apply_theme
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.app_shell import AppShell, MemoryShellSettings, NavMode, ShellSettings
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.icons import IconLoader, available_icons
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateLegend, PlateMode, TubeStrip
from gelinspect.ui.widgets.side_nav import DEFAULT_NAV, SideNav
from gelinspect.ui.widgets.sparkline import Sparkline
from gelinspect.ui.widgets.stat_card import ReadoutCard, StatCard
from gelinspect.ui.widgets.step_indicator import StepIndicator
from gelinspect.ui.widgets.tabs import Tabs
from gelinspect.ui.widgets.toast import Toast, ToastKind


class _Swatch(QWidget):
    """色板中的一格：色块加 token 名和色值。"""

    def __init__(self, name: str, value: str) -> None:
        """创建色板格。

        Args:
            name: token 名。
            value: 十六进制色值。
        """
        super().__init__()
        self._value = value
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 36, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(Label(name, "meta"))
        layout.addWidget(Label(value, "lab", mono=True))
        self.setMinimumWidth(112)
        self.setAccessibleName(f"{name} {value}")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制色块。

        Args:
            event: 绘制事件。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QColor(current_tokens().colors.border))
        p.setBrush(QColor(self._value))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, 30), 6, 6)
        p.end()


def temperature_series(count: int = 60) -> list[float]:
    """生成稳定在 37 ℃ 附近的示例温度序列（确定性，无随机数）。

    Args:
        count: 采样点数。

    Returns:
        温度列表。
    """
    return [37.0 + 0.12 * math.sin(i / 5.0) + 0.05 * math.sin(i * 1.7) for i in range(count)]


def _row(*widgets: QWidget, spacing: int = 12) -> QHBoxLayout:
    """把控件排成一行，末尾加弹簧。

    Args:
        *widgets: 控件。
        spacing: 间距。

    Returns:
        横向布局。
    """
    row = QHBoxLayout()
    row.setSpacing(spacing)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


class GalleryPage(QWidget):
    """组件展示的滚动内容。"""

    def __init__(self, report: FontReport | None) -> None:
        """创建展示页。

        Args:
            report: 字体加载报告，用于展示字体是否内置。
        """
        super().__init__()
        self.setProperty("role", "root")
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 28)
        col.setSpacing(14)
        col.addWidget(Label("GelInspect 设计系统 · 组件展示", "h1"))
        col.addLayout(self._tokens_and_type(report))
        col.addLayout(self._buttons_and_badges())
        col.addLayout(self._readouts())
        col.addWidget(self._plate_section())
        col.addLayout(self._wizard_and_tabs())
        col.addLayout(self._tables())
        col.addLayout(self._feedback_and_nav())
        col.addWidget(self._icons())
        col.addStretch(1)

    def _tokens_and_type(self, report: FontReport | None) -> QHBoxLayout:
        """色板与字体样张。

        Args:
            report: 字体报告。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(14)
        colors = Card("颜色 token")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        shown = [
            "bg",
            "surface",
            "border",
            "ink",
            "ink_muted",
            "primary",
            "primary_hover",
            "sidebar",
            "positive",
            "negative",
            "review",
            "review_soft",
            "danger",
            "success",
            "warning",
            "video_bg",
        ]
        tokens = current_tokens()
        for i, name in enumerate(shown):
            grid.addWidget(_Swatch(name, getattr(tokens.colors, name)), i // 4, i % 4)
        colors.body.addLayout(grid)
        row.addWidget(colors, 3)

        fonts = Card("字体")
        fonts.body.addWidget(Label("Noto Sans SC 常规：凝胶法细菌内毒素检测", "body"))
        medium = Label("Noto Sans SC 中等：试验监控 灵敏度复核", "val")
        fonts.body.addWidget(medium)
        fonts.body.addWidget(Label("Noto Sans SC 粗体：凝胶智检系统", "h2"))
        fonts.body.addWidget(
            Label("IBM Plex Mono  37.0 ℃  2605-01  E2604-3  0123456789", "body", mono=True)
        )
        status = "未获取字体报告"
        if report is not None:
            where = "内置" if report.sans_bundled else "系统回退"
            where_mono = "内置" if report.mono_bundled else "系统回退"
            status = f"正文 {report.sans}（{where}） · 等宽 {report.mono}（{where_mono}）"
            if report.missing:
                status += f" · 缺失：{', '.join(report.missing)}"
        fonts.body.addWidget(Label(status, "meta"))
        fonts.body.addStretch(1)
        row.addWidget(fonts, 2)
        return row

    def _buttons_and_badges(self) -> QHBoxLayout:
        """按钮、徽标与输入框。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(14)
        buttons = Card("按钮（高度 ≥ 44px，Tab 聚焦显示焦点环）")
        disabled = Button("不可用", ButtonVariant.PRIMARY)
        disabled.setEnabled(False)
        buttons.body.addLayout(
            _row(
                Button("新建试验", ButtonVariant.PRIMARY, icon="circle-plus"),
                Button("打开监控"),
                Button("停止试验", ButtonVariant.DANGER),
                disabled,
            )
        )
        field = QLineEdit()
        field.setPlaceholderText("扫码或输入批号")
        field.setMaximumWidth(260)
        buttons.body.addLayout(_row(Label("输入框", "lab"), field))
        row.addWidget(buttons, 3, Qt.AlignmentFlag.AlignTop)

        badges = Card("状态徽标 StatusBadge")
        tones = [
            ("第 2 排读数中", BadgeTone.INFO),
            ("已签署", BadgeTone.SUCCESS),
            ("待复核", BadgeTone.REVIEW),
            ("效期临近", BadgeTone.WARNING),
            ("已过期", BadgeTone.DANGER),
            ("空闲", BadgeTone.NEUTRAL),
        ]
        flow = QGridLayout()
        flow.setHorizontalSpacing(8)
        flow.setVerticalSpacing(8)
        for i, (text, tone) in enumerate(tones):
            flow.addWidget(StatusBadge(text, tone), i // 3, i % 3, Qt.AlignmentFlag.AlignLeft)
        badges.body.addLayout(flow)
        row.addWidget(badges, 2, Qt.AlignmentFlag.AlignTop)
        return row

    def _readouts(self) -> QHBoxLayout:
        """统计卡片、读数卡片与曲线。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(14)
        spark = Sparkline(temperature_series())
        readout = ReadoutCard(
            "检测室温度",
            "37.0",
            "℃",
            note="要求 37 ± 1 ℃ · <span style='color:#1E6B4A'>正常</span>",
            sparkline=spark,
        )
        row.addWidget(readout, 3)
        stats = QVBoxLayout()
        stats.setSpacing(14)
        stats.addWidget(StatCard("实测灵敏度 λc", "0.59", unit="EU/ml", note="标示 0.5 EU/ml"))
        stats.addWidget(StatCard("视频存储", "62%", note="剩余 3.1 TB"))
        row.addLayout(stats, 1)
        return row

    def _plate_section(self) -> Card:
        """孔板的全部状态、选择模式与试管条。

        Returns:
            卡片。
        """
        card = Card("孔板 PlateGrid（12 排 × 8 支，每排一竖列）")
        assert card.header is not None
        card.header.addWidget(PlateLegend())

        body = QHBoxLayout()
        body.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(Label("显示模式：点击发出 tubeClicked", "lab"))
        display = PlateGrid()
        display.set_states(monitor_states())
        display.set_countdown(1, "已读数")
        display.set_countdown(2, "读数中", active=True)
        display.set_countdown(3, "12:48")
        self.display_plate = display
        left.addWidget(display, 0, Qt.AlignmentFlag.AlignTop)
        left.addStretch(1)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(Label("选择模式：可切换，selectionChanged", "lab"))
        select = PlateGrid(PlateMode.SELECT)
        select.set_countdowns_visible(False)
        select.set_selected(range(1, 18))
        picked = Label("已选 17 / 17", "meta", mono=True)
        select.selectionChanged.connect(lambda tubes: picked.setText(f"已选 {len(tubes)}"))
        self.select_plate = select
        right.addWidget(select, 0, Qt.AlignmentFlag.AlignTop)
        right.addWidget(picked)
        right.addStretch(1)
        body.addLayout(right, 3)
        card.body.addLayout(body)

        extra = QHBoxLayout()
        extra.setSpacing(24)
        legend_rows = QVBoxLayout()
        legend_rows.addWidget(Label("六种状态单独放大（形状可区分，不依赖颜色）", "lab"))
        big = PlateGrid()
        big.set_countdowns_visible(False)
        big.setMinimumHeight(HOLES_PER_ROW * 48 + 24)
        big.set_states(
            {
                n: s
                for n, s in zip(
                    range(1, TUBE_COUNT + 1),
                    [
                        WellState.POSITIVE,
                        WellState.NEGATIVE,
                        WellState.REVIEW,
                        WellState.READING,
                        WellState.INCUBATING,
                        WellState.EMPTY,
                    ]
                    * 14,
                    strict=False,
                )
            }
        )
        legend_rows.addWidget(big, 0, Qt.AlignmentFlag.AlignTop)
        extra.addLayout(legend_rows, 3)
        strip = QVBoxLayout()
        strip.addWidget(Label("TubeStrip：暗底 8 连管", "lab"))
        strip.addWidget(
            TubeStrip(
                [0.62, 0.62, 0.0, 0.62, 0.3, 0.62, 0.45, 0.61],
                caption_left="Row02 · 翻转后",
                caption_right="4000×3000 · 15 fps",
            )
        )
        strip.addStretch(1)
        extra.addLayout(strip, 2)
        card.body.addLayout(extra)
        return card

    def _wizard_and_tabs(self) -> QVBoxLayout:
        """步骤条与标签栏。

        Returns:
            纵向布局。
        """
        col = QVBoxLayout()
        col.setSpacing(14)
        steps = Card("向导步骤 StepIndicator 与 标签 Tabs")
        names = ["选择模板", "校验试剂", "录入样品和管位", "确认启动"]
        steps.body.addWidget(StepIndicator(names, 0))
        steps.body.addWidget(StepIndicator(names, 2))
        steps.body.addWidget(StepIndicator(names, 3))
        steps.body.addWidget(Tabs(["试验数据记录", "图像显示", "报告打印预览", "单批次报告"]))
        col.addWidget(steps)
        return col

    def _tables(self) -> QHBoxLayout:
        """有数据与空状态的表格。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(14)
        data = Card("DataTable")
        table = DataTable(
            [
                Column("run", "试验", mono=True),
                Column("kind", "类型"),
                Column("tubes", "试管", mono=True, align_right=True),
                Column("result", "结果", kind="state"),
                Column("done", "完成时间", mono=True),
            ],
            fit_content=True,
        )
        table.set_rows(
            [
                {
                    "run": "FileId 64",
                    "kind": "限度试验",
                    "tubes": 8,
                    "result": WellState.NEGATIVE,
                    "done": "11-04 10:42",
                },
                {
                    "run": "FileId 63",
                    "kind": "灵敏度复核",
                    "tubes": 17,
                    "result": WellState.POSITIVE,
                    "done": "11-04 08:15",
                },
                {
                    "run": "FileId 62",
                    "kind": "半定量试验",
                    "tubes": 16,
                    "result": WellState.REVIEW,
                    "done": "11-03 17:26",
                },
            ]
        )
        data.body.addWidget(table)
        row.addWidget(data, 3)
        empty = Card("空状态")
        empty_table = DataTable(
            [Column("run", "试验"), Column("done", "完成时间")],
            empty_title="还没有历史试验",
            empty_hint="完成第一次试验后，结果会显示在这里",
        )
        empty.body.addWidget(empty_table)
        row.addWidget(empty, 2)
        return row

    def _feedback_and_nav(self) -> QHBoxLayout:
        """Toast、确认对话框与侧栏。

        Returns:
            横向布局。
        """
        row = QHBoxLayout()
        row.setSpacing(14)
        feedback = Card("Toast 与 ConfirmDialog")
        buttons = [
            Button("成功", icon="circle-check"),
            Button("警告", icon="triangle-alert"),
            Button("错误", icon="circle-x"),
        ]
        kinds = [ToastKind.SUCCESS, ToastKind.WARNING, ToastKind.DANGER]
        texts = ["试验已保存", "鲎试剂 2605-01 还有 12 天过期", "视频源断开，正在重连"]
        for b, k, t in zip(buttons, kinds, texts, strict=True):
            b.clicked.connect(lambda _=False, k=k, t=t: Toast.show_message(self.window(), t, k))
        feedback.body.addLayout(_row(*buttons))
        confirm = Button("停止试验…", ButtonVariant.DANGER)
        confirm.clicked.connect(
            lambda: ConfirmDialog.ask(
                self.window(),
                "停止试验？",
                "停止后已读数的结果会保留，未开始的排不会再检测。",
                confirm_text="停止试验",
                danger=True,
            )
        )
        feedback.body.addLayout(_row(confirm))
        feedback.body.addStretch(1)
        row.addWidget(feedback, 2, Qt.AlignmentFlag.AlignTop)

        nav = SideNav(DEFAULT_NAV[:5])
        nav.set_current("monitor")
        nav.setMinimumHeight(340)
        nav_wrap = QWidget()
        wrap_layout = QHBoxLayout(nav_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(nav)
        wrap_layout.addStretch(1)
        row.addWidget(nav_wrap, 1)
        return row

    def _icons(self) -> Card:
        """全部内置图标。

        Returns:
            卡片。
        """
        card = Card("图标 IconLoader（Lucide，随 token 着色）")
        flow = QGridLayout()
        flow.setHorizontalSpacing(18)
        flow.setVerticalSpacing(10)
        names = available_icons()
        for i, name in enumerate(names):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            icon = Label()
            icon.setPixmap(IconLoader.pixmap(name, 22, "primary" if i % 2 else "ink"))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = Label(name, "lab", mono=True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(icon)
            cell.addWidget(caption)
            holder = QWidget()
            holder.setLayout(cell)
            flow.addWidget(holder, i // 9, i % 9)
        card.body.addLayout(flow)
        return card


class GalleryWindow(QWidget):
    """展示窗口：AppShell 内容区顶部用标签切换“组件”与“试验监控小样”。"""

    def __init__(
        self,
        report: FontReport | None = None,
        *,
        settings: ShellSettings | None = None,
        mode: NavMode | None = None,
        animations_enabled: bool = True,
    ) -> None:
        """创建窗口。

        Args:
            report: 字体加载报告。
            settings: 侧栏模式持久化接口，默认 ``QSettings``。
            mode: 强制初始侧栏模式（截图用）。
            animations_enabled: 是否启用侧栏动画。
        """
        super().__init__()
        self.setProperty("role", "root")
        self.setWindowTitle("GelInspect 组件展示")
        content = QWidget()
        content.setProperty("role", "root")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(24, 8, 24, 0)
        self.tabs = Tabs(["组件", "试验监控小样"])
        bar_layout.addWidget(self.tabs, 1)
        column.addWidget(bar)
        self.stack = QStackedWidget()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.page = GalleryPage(report)
        self.scroll_area.setWidget(self.page)
        self.sample = MonitorPage()
        self.stack.addWidget(self.scroll_area)
        self.stack.addWidget(self.sample)
        column.addWidget(self.stack, 1)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)

        self.shell = AppShell(
            content, settings=settings, mode=mode, animations_enabled=animations_enabled
        )
        self.shell.nav.set_current("monitor")
        configure_demo_toolbar(self.shell.toolbar)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.shell)


def _parse_size(text: str) -> tuple[int, int]:
    """解析 ``1280x800`` 形式的尺寸。

    Args:
        text: 尺寸字符串。

    Returns:
        ``(宽, 高)``。

    Raises:
        argparse.ArgumentTypeError: 格式不正确。
    """
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"尺寸应形如 1280x800：{text}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """展示页入口。

    Args:
        argv: 命令行参数，默认取 ``sys.argv``。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(prog="gelinspect.ui.gallery", description="组件展示页")
    parser.add_argument("--screenshot", metavar="PATH", help="离屏截图保存路径，保存后退出")
    parser.add_argument(
        "--size", type=_parse_size, default=(1280, 800), help="窗口尺寸，如 1280x800"
    )
    parser.add_argument("--page", choices=("gallery", "monitor"), default="gallery")
    parser.add_argument(
        "--nav-mode",
        choices=("auto", "pinned"),
        default=None,
        help="侧栏模式：auto 自动隐藏、pinned 固定（默认读取已保存的设置，缺省 auto）",
    )
    parser.add_argument("--nav-open", action="store_true", help="自动隐藏模式下展开侧栏（截图用）")
    parser.add_argument("--full", action="store_true", help="截图时窗口高度撑满整个页面")
    parser.add_argument(
        "--scale", type=float, default=1.0, help="高 DPI 缩放倍数（QT_SCALE_FACTOR）"
    )
    args = parser.parse_args(argv)

    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(args.scale)

    app = QApplication(sys.argv[:1])
    report = apply_theme(app)
    mode = NavMode(args.nav_mode) if args.nav_mode else None
    shot = bool(args.screenshot)
    # 截图时用内存设置并关闭动画，不读写用户的真实偏好。
    settings: ShellSettings | None = MemoryShellSettings(mode) if shot else None
    window: MonitorSample | GalleryWindow
    if args.page == "monitor":
        window = MonitorSample(settings=settings, mode=mode, animations_enabled=not shot)
    else:
        window = GalleryWindow(
            last_report() or report, settings=settings, mode=mode, animations_enabled=not shot
        )
    width, height = args.size
    window.resize(width, height)
    window.show()
    app.processEvents()
    if args.nav_open:
        window.shell.open_nav(sticky=True)
        app.processEvents()
    if (focused := app.focusWidget()) is not None:
        focused.clearFocus()  # 初始焦点不画焦点环，截图更稳定

    if args.screenshot:
        if args.full:
            scroll = window.scroll_area
            inner = scroll.widget()
            assert inner is not None
            if (inner_layout := inner.layout()) is not None:
                inner_layout.activate()
            extra = window.height() - scroll.viewport().height()
            window.resize(width, extra + inner.sizeHint().height())
            app.processEvents()
        window.grab().save(args.screenshot)
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
