"""单管图像视图：裁剪图加可开关的算法掩码叠加（凝胶橙色填充，试管虚线轮廓）。

控件宽度按裁剪图的比例收窄，只在图像本身的区域绘制，两侧不留黑边。
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.ui.theme.fonts import sans_font
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.imaging import bgr_to_qimage


def polygons_path(polygons: tuple[NDArray[np.int32], ...]) -> QPainterPath:
    """把轮廓多边形合成一条路径（奇偶填充，带孔的轮廓正确）。

    Args:
        polygons: 每个元素是 N×2 的整数坐标。

    Returns:
        路径。
    """
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        path.moveTo(QPointF(float(polygon[0][0]), float(polygon[0][1])))
        for x, y in polygon[1:]:
            path.lineTo(QPointF(float(x), float(y)))
        path.closeSubpath()
    return path


_UNLIMITED = 16777215
"""Qt 的 ``QWIDGETSIZE_MAX``，PySide6 没有导出。"""
MIN_WIDTH = 48
"""控件的最小宽度。试管图很窄，太宽会留黑边。"""
MAX_HEIGHT = 380
"""控件的最大高度。"""


class TubeImageView(QWidget):
    """一张单管裁剪图，可叠加掩码。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建视图。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._image: QImage | None = None
        self._gel = QPainterPath()
        self._tube = QPainterPath()
        self._overlay = True
        self._text = ""
        self.setMinimumSize(MIN_WIDTH, 240)
        self.setMaximumHeight(MAX_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_content(
        self,
        image: NDArray[np.uint8] | None,
        gel: tuple[NDArray[np.int32], ...] = (),
        tube: tuple[NDArray[np.int32], ...] = (),
    ) -> None:
        """设置图像和掩码轮廓。

        Args:
            image: BGR 裁剪图，None 表示没有图。
            gel: 凝胶轮廓（图像坐标）。
            tube: 试管轮廓。
        """
        self._image = None if image is None else bgr_to_qimage(image)
        self._gel = polygons_path(gel)
        self._tube = polygons_path(tube)
        if self._image is None:
            self.setMaximumWidth(_UNLIMITED)
        else:
            aspect = self._image.width() / max(1, self._image.height())
            self.setMaximumWidth(max(MIN_WIDTH, round(MAX_HEIGHT * aspect)))
        self.updateGeometry()
        self.update()

    def has_image(self) -> bool:
        """是否有图像。

        Returns:
            有图像时为 True。
        """
        return self._image is not None

    def has_overlay_data(self) -> bool:
        """是否有可叠加的掩码。

        Returns:
            凝胶或试管轮廓不为空时为 True。
        """
        return not (self._gel.isEmpty() and self._tube.isEmpty())

    def set_overlay_visible(self, visible: bool) -> None:
        """开关掩码叠加。

        Args:
            visible: 是否显示。
        """
        self._overlay = visible
        self.update()

    def overlay_visible(self) -> bool:
        """掩码叠加是否打开。

        Returns:
            打开时为 True。
        """
        return self._overlay

    def set_text(self, text: str) -> None:
        """没有图时的说明。

        Args:
            text: 说明文字。
        """
        self._text = text
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            尺寸。
        """
        if self._image is None:
            return QSize(240, 340)
        aspect = self._image.width() / max(1, self._image.height())
        return QSize(max(MIN_WIDTH, round(MAX_HEIGHT * aspect)), MAX_HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制图像和叠加层。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setPen(Qt.PenStyle.NoPen)
        image = self._image
        if image is None:
            p.setBrush(QColor(c.video_bg))
            p.drawRoundedRect(QRectF(self.rect()), 3, 3)
            p.setFont(sans_font(13))
            p.setPen(QColor(c.video_text))
            p.drawText(
                QRectF(self.rect()).adjusted(10, 0, -10, 0),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                self._text,
            )
            p.end()
            return
        area = QRectF(self.rect())
        scale = min(area.width() / image.width(), area.height() / image.height())
        w, h = image.width() * scale, image.height() * scale
        target = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
        p.drawImage(target, image)
        if self._overlay:
            p.save()
            p.translate(target.topLeft())
            p.scale(scale, scale)
            fill = QColor(c.gel_overlay)
            fill.setAlphaF(0.38)
            p.setPen(QPen(QColor(c.positive), 2.0 / scale))
            p.setBrush(fill)
            p.drawPath(self._gel)
            dashed = QPen(QColor(c.primary), 2.0 / scale)
            dashed.setDashPattern([6.0, 5.0])
            p.setPen(dashed)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._tube)
            p.restore()
        p.end()
