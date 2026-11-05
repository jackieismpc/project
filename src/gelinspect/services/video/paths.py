"""媒体文件路径：标识校验、防路径穿越、不覆盖已有文件。"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from gelinspect.domain.plate import ROW_COUNT
from gelinspect.services.video.errors import InvalidIdentifierError, RecorderError

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_experiment_id(experiment_id: str) -> str:
    """校验试验标识可以安全地作为目录名。

    只允许字母、数字、``_``、``.``、``-``，首字符必须是字母或数字，长度 1–64，且不含 ``..``。

    Args:
        experiment_id: 试验标识。

    Returns:
        原样返回通过校验的标识。

    Raises:
        InvalidIdentifierError: 标识不合法，例如含路径分隔符或 ``..``。
    """
    if not _ID_PATTERN.match(experiment_id) or ".." in experiment_id:
        raise InvalidIdentifierError(f"试验标识不合法：{experiment_id!r}")
    return experiment_id


def validate_row_index(row_index: int) -> int:
    """校验排号在 1 到 ``ROW_COUNT`` 之间。

    Args:
        row_index: 排号。

    Returns:
        原样返回。

    Raises:
        InvalidIdentifierError: 排号越界。
    """
    if isinstance(row_index, bool) or not 1 <= row_index <= ROW_COUNT:
        raise InvalidIdentifierError(f"排号应在 1–{ROW_COUNT} 内：{row_index!r}")
    return row_index


def ensure_inside(root: Path, path: Path) -> Path:
    """确认 ``path`` 解析后位于 ``root`` 之内（兜底，防符号链接逃逸）。

    Args:
        root: 根目录。
        path: 待检查的路径。

    Returns:
        解析后的绝对路径。

    Raises:
        InvalidIdentifierError: 路径落在根目录之外。
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise InvalidIdentifierError(f"路径越出数据目录：{path}")
    return resolved


def experiment_dir(data_dir: Path, kind: str, experiment_id: str) -> Path:
    """返回并创建 ``<data_dir>/<kind>/<experiment_id>``。

    Args:
        data_dir: 数据根目录。
        kind: ``videos`` 或 ``images``。
        experiment_id: 试验标识，会先校验。

    Returns:
        已创建的目录（解析后的绝对路径）。

    Raises:
        InvalidIdentifierError: 标识不合法，或路径越界。
    """
    validate_experiment_id(experiment_id)
    base = data_dir / kind
    target = ensure_inside(base, base / experiment_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def next_free_name(directory: Path, stem: str, suffix: str) -> str:
    """返回目录下尚不存在的文件名，已有文件绝不覆盖。

    先尝试 ``<stem><suffix>``，已存在时依次尝试 ``<stem>_02<suffix>``、``<stem>_03<suffix>``……
    同时避开遗留的 ``.part`` 文件。

    Args:
        directory: 目录。
        stem: 文件名主干，例如 ``row_01``。
        suffix: 后缀，例如 ``.mp4``，目录可传空串。

    Returns:
        可用的文件名。
    """
    attempt = 1
    while True:
        name = f"{stem}{suffix}" if attempt == 1 else f"{stem}_{attempt:02d}{suffix}"
        if not (directory / name).exists() and not (directory / f"{name}.part").exists():
            return name
        attempt += 1


def fsync_directory(directory: Path) -> None:
    """尽力把目录项刷到磁盘（Windows 不支持，静默跳过）。

    Args:
        directory: 目录。
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def fsync_file(path: Path) -> None:
    """把文件内容刷到磁盘（以读写方式打开，兼容 Windows）。

    Args:
        path: 文件路径。
    """
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def sha256_file(path: Path) -> str:
    """流式计算文件的 SHA-256。

    Args:
        path: 文件路径。

    Returns:
        64 位小写十六进制。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def needs_ascii_path(path: Path) -> bool:
    """判断 ``cv2.VideoWriter`` 能否直接写这个路径。

    OpenCV 在 Windows 上用 ANSI 接口打开文件，路径含中文等非 ASCII 字符时会失败；
    macOS 和 Linux 使用 UTF-8，没有这个问题。

    Args:
        path: 目标路径。

    Returns:
        需要改用纯 ASCII 临时路径写入时为 True。
    """
    return os.name == "nt" and not str(path).isascii()


def make_ascii_scratch_dir() -> Path:
    """创建一个路径纯 ASCII 的临时目录，供不支持中文路径的编码器写文件。

    系统临时目录常常位于含中文用户名的目录下，所以依次尝试临时目录、公共目录、
    ``ProgramData`` 和系统盘根目录，取第一个路径为纯 ASCII 且可写的。

    Returns:
        新建的目录，调用方负责删除。

    Raises:
        RecorderError: 找不到可用的纯 ASCII 目录。
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
            return Path(tempfile.mkdtemp(prefix="gelinspect_", dir=candidate))
        except OSError:
            continue
    raise RecorderError(
        "数据目录含非 ASCII 字符，而 OpenCV 在 Windows 上无法写这样的路径，"
        "且找不到纯 ASCII 的临时目录；请把数据目录改为纯英文路径"
    )
