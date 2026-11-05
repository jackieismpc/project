"""按排回放：把一排录制的 MP4 逐帧解码出来，界面据此播放。

用 OpenCV 解码，不依赖 QtMultimedia。打开时先核对入库的 SHA-256，被改动的录像不播放。
Windows 下 OpenCV 打不开含中文的路径，此时先把文件复制（同卷硬链接）到纯 ASCII 临时目录再打开，
关闭时删除，做法同视频文件源。解码出的帧缩到长边不超过 ``long_side``，播放不需要原始分辨率。
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.services.video.paths import make_ascii_scratch_dir, needs_ascii_path, sha256_file
from gelinspect.services.video.preview import resize_to_long_side


class ReplayError(Exception):
    """录像无法回放：文件缺失、哈希不符或无法解码。"""


class VideoClip:
    """一段可逐帧读取、可定位的录像。

    不是线程安全的，只在一个线程里使用（界面主线程）。
    """

    def __init__(self, path: Path, *, sha256: str | None = None, long_side: int = 960) -> None:
        """打开录像。

        Args:
            path: MP4 路径。
            sha256: 入库的哈希，不为 None 时核对。
            long_side: 解码后帧的长边上限。

        Raises:
            ReplayError: 文件缺失、哈希不符或无法解码。
        """
        if not path.is_file():
            raise ReplayError(f"录像文件不存在：{path}")
        if sha256 is not None and sha256_file(path) != sha256:
            raise ReplayError(f"录像 {path.name} 与入库的 SHA-256 不符，可能已被改动")
        self._path = path
        self._long_side = long_side
        self._scratch: Path | None = None
        target = path
        if needs_ascii_path(path):
            self._scratch = make_ascii_scratch_dir()
            target = self._scratch / "clip.mp4"
            try:
                os.link(path, target)
            except OSError:
                shutil.copyfile(path, target)
        self._capture = cv2.VideoCapture(str(target))
        if not self._capture.isOpened():
            self.close()
            raise ReplayError(f"无法解码录像：{path.name}")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        self._fps = fps if fps > 0 else 10.0
        self._count = max(0, int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        self._width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._position = 0

    @property
    def path(self) -> Path:
        """录像路径。"""
        return self._path

    @property
    def fps(self) -> float:
        """帧率。"""
        return self._fps

    @property
    def frame_count(self) -> int:
        """总帧数。"""
        return self._count

    @property
    def size(self) -> tuple[int, int]:
        """原始分辨率 ``(宽, 高)``。"""
        return self._width, self._height

    @property
    def position(self) -> int:
        """下一次 :meth:`read_next` 读取的帧序号。"""
        return self._position

    @property
    def duration_s(self) -> float:
        """总时长，秒。"""
        return self._count / self._fps

    def read_next(self) -> NDArray[np.uint8] | None:
        """解码下一帧。

        Returns:
            BGR 图像（已缩小），到末尾时为 None。
        """
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        self._position += 1
        return resize_to_long_side(np.asarray(frame, dtype=np.uint8), self._long_side)

    def seek(self, index: int) -> None:
        """定位到某一帧，下一次 :meth:`read_next` 读它。

        Args:
            index: 帧序号，超出范围时取边界。
        """
        last = max(0, self._count - 1)
        target = max(0, min(index, last))
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, target)
        self._position = target

    def close(self) -> None:
        """释放解码器并删除临时副本，可重复调用。"""
        capture = getattr(self, "_capture", None)
        if capture is not None:
            capture.release()
        if self._scratch is not None:
            with contextlib.suppress(OSError):
                shutil.rmtree(self._scratch)
            self._scratch = None
