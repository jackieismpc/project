"""接口层的数据契约基类和 ndarray 字段类型。

ndarray 字段使用 O(1) 校验（类型、维度、dtype），不复制也不遍历像素，
不会占用算法 2 秒的耗时预算。掩码的像素值检查放在
:func:`gelinspect.interfaces.analysis.check_row_result` 中。
"""

from __future__ import annotations

from typing import Annotated, Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, PlainValidator


class ContractModel(BaseModel):
    """数据契约基类：不可变、拒绝未知字段、允许 ndarray 字段。

    不可变只约束字段重新赋值，ndarray 和 list 的内容仍可修改，调用方不得修改。
    含 ndarray 的模型不能用 ``==`` 比较，也不能序列化为 JSON。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


def _validate_bgr_image(value: Any) -> NDArray[np.uint8]:
    """校验 BGR 图像，原样返回同一个数组。

    Args:
        value: 待校验的值。

    Returns:
        输入数组本身，不复制。

    Raises:
        ValueError: 不是 H×W×3 的 uint8 ndarray，或尺寸为空。
    """
    if not isinstance(value, np.ndarray):
        raise ValueError(f"图像必须是 numpy.ndarray，实际为 {type(value).__name__}")
    if value.dtype != np.uint8:
        raise ValueError(f"图像 dtype 必须是 uint8，实际为 {value.dtype}")
    if value.ndim != 3 or value.shape[2] != 3 or value.size == 0:
        raise ValueError(f"图像形状必须是 H×W×3，实际为 {value.shape}")
    return value


def _validate_mask(value: Any) -> NDArray[np.uint8]:
    """校验二值掩码的类型和维度，原样返回同一个数组。

    Args:
        value: 待校验的值。

    Returns:
        输入数组本身，不复制。

    Raises:
        ValueError: 不是 H×W 的 uint8 ndarray，或尺寸为空。
    """
    if not isinstance(value, np.ndarray):
        raise ValueError(f"掩码必须是 numpy.ndarray，实际为 {type(value).__name__}")
    if value.dtype != np.uint8:
        raise ValueError(f"掩码 dtype 必须是 uint8，实际为 {value.dtype}")
    if value.ndim != 2 or value.size == 0:
        raise ValueError(f"掩码形状必须是 H×W，实际为 {value.shape}")
    return value


BgrImage = Annotated[NDArray[np.uint8], PlainValidator(_validate_bgr_image)]
"""H×W×3、uint8、BGR 通道顺序的图像。"""

BinaryMask = Annotated[NDArray[np.uint8], PlainValidator(_validate_mask)]
"""H×W、uint8、取值只有 0 和 255 的掩码。"""
