"""图片读写：支持含中文（非 ASCII）的路径。

Windows 下 ``cv2.imread`` 和 ``cv2.imwrite`` 不支持非 ASCII 路径，这里统一用 Python 文件 I/O
加 ``cv2.imdecode`` / ``cv2.imencode``。
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.video import VideoError

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


class ImageLoadError(VideoError):
    """图片缺失、损坏、格式不支持，或一组图片不满足要求。"""


def read_image(path: Path) -> NDArray[np.uint8]:
    """读取图片为 H×W×3、uint8、BGR（灰度图转三通道，忽略 alpha）。

    Args:
        path: 图片路径，可含中文。

    Returns:
        图像数组。

    Raises:
        ImageLoadError: 文件不存在、读取失败或无法解码。
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError as exc:
        raise ImageLoadError(f"无法读取图片 {path}：{exc}") from exc
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None or image.size == 0:
        raise ImageLoadError(f"图片损坏或格式不支持：{path}")
    return np.asarray(image, dtype=np.uint8)


def write_image(path: Path, image: NDArray[np.uint8], *, quality: int = 95) -> None:
    """写图片（按扩展名选择 PNG/JPG），路径可含中文，自动创建父目录。

    Args:
        path: 目标路径。
        image: BGR 图像。
        quality: JPG 质量。

    Raises:
        ImageLoadError: 编码或写盘失败。
    """
    suffix = path.suffix.lower()
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if suffix in {".jpg", ".jpeg"} else []
    ok, encoded = cv2.imencode(suffix, image, params)
    if not ok:
        raise ImageLoadError(f"图片编码失败：{path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded.tofile(path)
    except OSError as exc:
        raise ImageLoadError(f"写图片失败 {path}：{exc}") from exc


def _natural_key(path: Path) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def list_images(directory: Path) -> list[Path]:
    """列出目录下的 JPG/PNG 文件，按自然排序（``img2`` 在 ``img10`` 之前）。

    Args:
        directory: 目录。

    Returns:
        文件路径列表；目录不存在时抛异常。

    Raises:
        ImageLoadError: 目录不存在。
    """
    if not directory.is_dir():
        raise ImageLoadError(f"图片目录不存在：{directory}")
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    return sorted(files, key=_natural_key)
