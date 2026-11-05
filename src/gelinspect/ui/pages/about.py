"""关于页：版本、运行环境、已加载的后端和开源许可。"""

from __future__ import annotations

import platform

import PySide6
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from gelinspect import __version__
from gelinspect.services.app_services import AppServices
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.label import Label

_LICENSES = (
    ("Qt for Python (PySide6)", "LGPL-3.0"),
    ("Noto Sans SC、IBM Plex Mono", "SIL OFL 1.1"),
    ("Lucide Icons", "ISC"),
)


class AboutPage(QWidget):
    """关于页。"""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务，提供目录和后端信息。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
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
        self.title = Label("", "h1")
        col.addWidget(self.title)

        self.product_card = Card()
        self.product_name = Label("", "val")
        self.product_desc = Label("", "body")
        self.product_desc.setWordWrap(True)
        self.product_version = Label("", "meta", mono=True)
        self.product_card.body.setSpacing(4)
        for w in (self.product_name, self.product_desc, self.product_version):
            self.product_card.body.addWidget(w)
        col.addWidget(self.product_card)

        self.env_card = Card(" ")
        self.env_grid = QGridLayout()
        self.env_grid.setHorizontalSpacing(24)
        self.env_grid.setVerticalSpacing(8)
        self.env_grid.setColumnStretch(1, 1)
        self.env_card.body.addLayout(self.env_grid)
        self._env_labels: list[tuple[Label, Label]] = []
        for _ in range(8):
            name, value = Label("", "lab"), Label("", "body", mono=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self._env_labels.append((name, value))
        col.addWidget(self.env_card)

        self.license_card = Card(" ")
        self._license_rows: list[tuple[Label, Label]] = []
        for product, kind in _LICENSES:
            row = QHBoxLayout()
            left, right = Label(product, "body"), Label(kind, "meta", mono=True)
            row.addWidget(left, 1)
            row.addWidget(right)
            self.license_card.body.addLayout(row)
            self._license_rows.append((left, right))
        col.addWidget(self.license_card)
        col.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("关于"))
        self.product_name.setText(self.tr("凝胶智检系统（GelInspect）"))
        self.product_desc.setText(
            self.tr("凝胶法细菌内毒素检测的上位机软件：试验管理、视频采集、算法判读、复核与报告。")
        )
        self.product_version.setText(self.tr("版本 {version}").format(version=__version__))
        self.env_card.set_title(self.tr("运行环境"))
        self.license_card.set_title(self.tr("开源许可"))
        s = self._services
        rows = [
            (self.tr("系统"), f"{platform.system()} {platform.release()}"),
            (self.tr("Python"), platform.python_version()),
            (self.tr("Qt / PySide6"), PySide6.__version__),
            (self.tr("数据目录"), str(s.data_dir)),
            (self.tr("日志目录"), str(s.log_dir)),
            (self.tr("设备后端"), s.backends.get("device", "-")),
            (self.tr("视频源"), s.backends.get("video", "-")),
            (self.tr("算法后端"), s.backends.get("analysis", "-")),
        ]
        for i, ((label, value), (text, content)) in enumerate(
            zip(self._env_labels, rows, strict=True)
        ):
            label.setText(text)
            value.setText(content)
            if self.env_grid.itemAtPosition(i, 0) is None:
                self.env_grid.addWidget(label, i, 0, Qt.AlignmentFlag.AlignTop)
                self.env_grid.addWidget(value, i, 1)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
