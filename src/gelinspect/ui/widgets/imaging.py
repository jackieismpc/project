"""numpy 图像与 QImage 的转换。"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage


def bgr_to_qimage(image: NDArray[np.uint8]) -> QImage:
    """BGR 的 numpy 图像转成独立持有像素的 QImage。

    Args:
        image: H×W×3 的 uint8 BGR 图像，或 H×W 的灰度图。

    Returns:
        拷贝了像素的 QImage。
    """
    data = np.ascontiguousarray(image)
    height, width = data.shape[:2]
    if data.ndim == 2:
        return QImage(data.data, width, height, width, QImage.Format.Format_Grayscale8).copy()
    return QImage(data.data, width, height, width * 3, QImage.Format.Format_BGR888).copy()
