"""视频源共用的图像规整函数。"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray


def normalize_bgr(image: object) -> NDArray[np.uint8] | None:
    """把读到的图像规整为 H×W×3、uint8、BGR。

    灰度图转为三通道，BGRA 去掉 alpha；其他格式（如 16 位）无法规整。

    Args:
        image: ``VideoCapture.read`` 返回的图像。

    Returns:
        规整后的数组（已是 BGR 时为原数组）；无法规整时为 None。
    """
    if not isinstance(image, np.ndarray) or image.size == 0 or image.dtype != np.uint8:
        return None
    if image.ndim == 2:
        return np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))
    if image.ndim == 3 and image.shape[2] == 3:
        return image
    if image.ndim == 3 and image.shape[2] == 4:
        return np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGRA2BGR))
    return None
