"""让只支持 ASCII 路径的 OpenCV 接口读取含中文的视频文件。

OpenCV 在 Windows 上用 ANSI 接口打开文件，``cv2.VideoCapture`` 打不开含中文的路径。
macOS 和 Linux 没有这个问题。解决办法是把文件链接（同卷时）或复制（跨卷时）到纯 ASCII 的
临时目录再打开；播放结束时删除临时副本。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def needs_ascii_path(path: Path) -> bool:
    """判断 ``cv2.VideoCapture`` 能否直接打开这个路径。

    Args:
        path: 文件路径。

    Returns:
        Windows 下路径含非 ASCII 字符时为 True。
    """
    return os.name == "nt" and not str(path).isascii()


def link_to_ascii_path(path: Path) -> Path:
    """在纯 ASCII 临时目录里为 ``path`` 建立硬链接，失败则复制。

    Args:
        path: 源文件。

    Returns:
        临时副本路径；其父目录由调用方用 :func:`discard_scratch` 删除。

    Raises:
        OSError: 找不到纯 ASCII 的可写目录，或复制失败。
    """
    candidates = [
        tempfile.gettempdir(),
        os.environ.get("PUBLIC"),
        os.environ.get("PROGRAMDATA"),
        (os.environ.get("SYSTEMDRIVE") or "C:") + os.sep,
    ]
    for candidate in candidates:
        if not candidate or not candidate.isascii() or not os.path.isdir(candidate):
            continue
        try:
            scratch = Path(tempfile.mkdtemp(prefix="gelinspect_", dir=candidate))
        except OSError:
            continue
        target = scratch / f"video{path.suffix.lower()}"
        try:
            try:
                os.link(path, target)
            except OSError:
                shutil.copyfile(path, target)
        except OSError:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        return target
    raise OSError("找不到纯 ASCII 的临时目录，请把视频文件移到纯英文路径")


def discard_scratch(path: Path) -> None:
    """删除 :func:`link_to_ascii_path` 创建的临时目录。

    Args:
        path: 临时副本路径。
    """
    shutil.rmtree(path.parent, ignore_errors=True)
