"""视频显示区：暗底，图像按比例居中缩放，没有画面时显示说明。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.ui.theme.fonts import sans_font
from gelinspect.ui.theme.tokens import current_tokens


class VideoView(QWidget):
    """显示一帧 QImage 的暗底视图，供实时预览和回放共用。

    只在主线程调用 :meth:`set_image`。图像不拷贝，持有引用直到下一帧。
    """

    def __init__(
        self, parent: QWidget | None = None, *, min_height: int = 190, max_height: int | None = None
    ) -> None:
        """创建视图。

        Args:
            parent: 父控件。
            min_height: 最小高度。
            max_height: 最大高度，None 表示不限。
        """
        super().__init__(parent)
        self._image: QImage | None = None
        self._text = ""
        self._frames = 0
        self.setMinimumHeight(min_height)
        if max_height is not None:
            self.setMaximumHeight(max_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    @property
    def frames_shown(self) -> int:
        """已显示的帧数。"""
        return self._frames

    def image(self) -> QImage | None:
        """当前显示的图像。

        Returns:
            图像，没有时为 None。
        """
        return self._image

    def set_image(self, image: QImage | None) -> None:
        """显示一帧；传 None 清空。

        Args:
            image: 图像。
        """
        self._image = image
        if image is not None:
            self._frames += 1
        self.update()

    def set_text(self, text: str) -> None:
        """没有画面时显示的说明。

        Args:
            text: 说明文字。
        """
        self._text = text
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸，按 16:9。

        Returns:
            尺寸。
        """
        return QSize(420, 236)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.video_bg))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        image = self._image
        if image is None or image.isNull():
            p.setFont(sans_font(13))
            p.setPen(QColor(c.video_text))
            p.drawText(
                QRectF(self.rect()).adjusted(12, 0, -12, 0),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                self._text,
            )
        else:
            area = QRectF(self.rect()).adjusted(6, 6, -6, -6)
            scale = min(area.width() / image.width(), area.height() / image.height())
            w, h = image.width() * scale, image.height() * scale
            target = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
            p.drawImage(target, image)
        p.end()
