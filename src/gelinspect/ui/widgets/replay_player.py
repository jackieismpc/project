"""小型播放器：播放一排录制的 MP4，带播放/暂停、进度条和时间。

解码由服务层的 :class:`~gelinspect.services.replay.VideoClip`（OpenCV）完成，这里用定时器
逐帧取图转成 QImage 显示，不使用 QtMultimedia。解码很快（录像已缩到长边 1920），在主线程做。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QSlider, QVBoxLayout, QWidget

from gelinspect.services.replay import VideoClip
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.imaging import bgr_to_qimage
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.video_view import VideoView


def format_clock(seconds: float) -> str:
    """把秒数格式化为 ``mm:ss.s``。

    Args:
        seconds: 秒。

    Returns:
        例如 ``00:03.4``。
    """
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


class ReplayPlayer(QWidget):
    """录像播放器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建播放器。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._clip: VideoClip | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.view = VideoView(min_height=220)
        layout.addWidget(self.view)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.play_button = Button("", ButtonVariant.SECONDARY, icon="play")
        self.play_button.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(self._on_slider)
        self.time_label = Label("", "meta", mono=True)
        controls.addWidget(self.play_button)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time_label)
        layout.addLayout(controls)
        self.retranslate()
        self._sync_controls()

    # ------------------------------------------------------------------ 状态

    @property
    def clip(self) -> VideoClip | None:
        """当前录像。"""
        return self._clip

    @property
    def is_playing(self) -> bool:
        """是否正在播放。"""
        return self._timer.isActive()

    @property
    def position(self) -> int:
        """已显示到第几帧（从 0 起，最后显示的一帧的序号）。"""
        return self.slider.value()

    def load(self, clip: VideoClip, *, autoplay: bool = True) -> None:
        """载入录像并显示第一帧。

        Args:
            clip: 录像；播放器接管它，换片或关闭时释放。
            autoplay: 是否立刻播放。
        """
        self.unload()
        self._clip = clip
        count = max(1, clip.frame_count)
        self._updating = True
        self.slider.setRange(0, count - 1)
        self.slider.setValue(0)
        self._updating = False
        self._show_next()
        self._sync_controls()
        if autoplay:
            self.play()

    def unload(self) -> None:
        """停止并释放录像。"""
        self._timer.stop()
        if self._clip is not None:
            self._clip.close()
            self._clip = None
        self.view.set_image(None)
        self._updating = True
        self.slider.setRange(0, 0)
        self._updating = False
        self._sync_controls()

    def show_message(self, text: str) -> None:
        """没有录像时显示说明。

        Args:
            text: 说明文字。
        """
        self.unload()
        self.view.set_text(text)

    # ------------------------------------------------------------------ 播放

    def play(self) -> None:
        """播放；已到末尾时从头开始。"""
        clip = self._clip
        if clip is None:
            return
        if clip.position >= clip.frame_count:
            clip.seek(0)
            self._show_next()
        self._timer.start(max(10, round(1000 / clip.fps)))
        self._sync_controls()

    def pause(self) -> None:
        """暂停。"""
        self._timer.stop()
        self._sync_controls()

    def toggle(self) -> None:
        """播放与暂停互换。"""
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def _advance(self) -> None:
        if not self._show_next():
            self.pause()

    def _show_next(self) -> bool:
        clip = self._clip
        if clip is None:
            return False
        index = clip.position
        frame = clip.read_next()
        if frame is None:
            return False
        self.view.set_image(bgr_to_qimage(frame))
        self._updating = True
        self.slider.setValue(min(index, self.slider.maximum()))
        self._updating = False
        self._sync_controls()
        return True

    def _on_slider(self, value: int) -> None:
        clip = self._clip
        if self._updating or clip is None:
            return
        clip.seek(value)
        self._show_next()

    def _sync_controls(self) -> None:
        clip = self._clip
        has_clip = clip is not None
        for widget in (self.play_button, self.slider):
            widget.setEnabled(has_clip)
        self.play_button.setText(self.tr("暂停") if self.is_playing else self.tr("播放"))
        if clip is None:
            self.time_label.setText("")
            return
        now = clip.position / clip.fps
        self.time_label.setText(f"{format_clock(now)} / {format_clock(clip.duration_s)}")

    def retranslate(self) -> None:
        """刷新文字。"""
        self.play_button.setAccessibleName(self.tr("播放或暂停"))
        self.slider.setAccessibleName(self.tr("回放进度"))
        self._sync_controls()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        """隐藏时暂停，避免在后台继续解码。

        Args:
            event: 事件。
        """
        self.pause()
        super().hideEvent(event)  # type: ignore[arg-type]
