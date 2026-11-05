"""按排回放对话框：选择一排，播放它的录像。非模态，不阻塞界面。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.domain.plate import row_label
from gelinspect.services.auth import Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.media_service import MediaService
from gelinspect.services.replay import VideoClip
from gelinspect.ui.labels import describe_service_error
from gelinspect.ui.widgets.button import Button
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.replay_player import ReplayPlayer


class ReplayDialog(QDialog):
    """回放对话框。"""

    def __init__(
        self,
        media: MediaService,
        session: Session | None,
        experiment_id: str,
        rows: Sequence[int],
        initial_row: int,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框并开始播放初始排。

        Args:
            media: 媒体服务。
            session: 当前会话。
            experiment_id: 试验标识。
            rows: 有录像的排号。
            initial_row: 初始排。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(False)
        self.setMinimumWidth(560)
        self._media = media
        self._session = session
        self._experiment_id = experiment_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        self.title = Label("", "h2")
        self.rows = QComboBox()
        for row in rows:
            self.rows.addItem(row_label(row), row)
        index = self.rows.findData(initial_row)
        self.rows.setCurrentIndex(max(0, index))
        self.rows.currentIndexChanged.connect(lambda _i: self.play_current())
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.rows)
        layout.addLayout(head)
        self.player = ReplayPlayer()
        layout.addWidget(self.player)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.close_button = Button("")
        self.close_button.clicked.connect(self.close)
        foot.addWidget(self.close_button)
        layout.addLayout(foot)
        self.retranslate()
        self.play_current()

    def current_row(self) -> int:
        """正在播放的排号。

        Returns:
            排号，没有录像时为 0。
        """
        data = self.rows.currentData()
        return int(data) if data is not None else 0

    def play_current(self) -> None:
        """播放下拉框里选中的排。"""
        row = self.current_row()
        if row == 0:
            self.player.show_message(self.tr("这一排没有录像"))
            return
        try:
            clip: VideoClip = self._media.open_clip(self._session, self._experiment_id, row)
        except ServiceError as error:
            self.player.show_message(describe_service_error(error))
            return
        self.player.load(clip)

    def retranslate(self) -> None:
        """刷新文字。"""
        self.setWindowTitle(self.tr("按排回放"))
        self.title.setText(self.tr("按排回放"))
        self.close_button.setText(self.tr("关闭"))

    def done(self, result: int) -> None:
        """关闭时释放录像。

        Args:
            result: 对话框结果。
        """
        self.player.unload()
        super().done(result)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
