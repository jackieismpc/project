"""算法掩码存档：把每支试管的代表帧掩码存成 PNG，并生成媒体记录。

``TubeAnalysis.masks`` 是翻转前后代表帧上的 gel 和 tube 掩码（uint8，0/255，与原图同尺寸）。
引擎把它们存到该排原图所在的目录 ``<data_dir>/images/<试验>/row_XX/``，文件名
``mask_tNN_<gel|tube>_<before|after>.png``，计算 SHA-256，随分析结果在同一个工作单元里入库。
界面据此叠加显示，之后的报告和复查也能取到当时算法看到的区域。

文件先写 ``.part`` 再 fsync、改名，已有文件不覆盖。任何一个文件写失败都会删掉本次已写的文件
并抛 :class:`MaskArchiveError`，调用方按分析失败处理（原图还在，可以重试）。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.analysis import RowAnalysisRequest, RowAnalysisResult, TubeMasks
from gelinspect.interfaces.storage import MediaFile, MediaKind
from gelinspect.services.video.paths import fsync_directory, next_free_name

MASK_PREFIX = "mask_"
"""掩码文件名前缀，用来和 ``before_NN`` / ``after_NN`` 原图区分。"""

MaskKind = Literal["gel", "tube"]
MaskPhase = Literal["before", "after"]

_NAME = re.compile(
    r"^mask_t(?P<tube>\d{2})_(?P<kind>gel|tube)_(?P<phase>before|after)(?:_\d{2})?\.png$"
)
_PNG_COMPRESSION = 1
"""掩码是二值图，压缩等级 1 已经很小，同时不占用分析线程太久。"""


class MaskArchiveError(Exception):
    """掩码存档失败：无法确定目录、编码失败或写盘失败。"""


def mask_file_name(tube_no: int, kind: MaskKind, phase: MaskPhase) -> str:
    """掩码文件的标准名称。

    Args:
        tube_no: 管号。
        kind: ``gel`` 或 ``tube``。
        phase: ``before`` 为翻转前，``after`` 为翻转后。

    Returns:
        例如 ``mask_t07_gel_before.png``。
    """
    return f"{MASK_PREFIX}t{tube_no:02d}_{kind}_{phase}.png"


def parse_mask_name(name: str) -> tuple[int, MaskKind, MaskPhase] | None:
    """解析掩码文件名。

    Args:
        name: 文件名（不含目录）。

    Returns:
        ``(管号, 种类, 阶段)``，不是掩码文件时为 None。重名时带 ``_02`` 后缀的也能解析。
    """
    match = _NAME.match(name)
    if match is None:
        return None
    kind: MaskKind = "gel" if match["kind"] == "gel" else "tube"
    phase: MaskPhase = "before" if match["phase"] == "before" else "after"
    return int(match["tube"]), kind, phase


def _layers(masks: TubeMasks) -> list[tuple[MaskKind, MaskPhase, NDArray[np.uint8]]]:
    layers: list[tuple[MaskKind, MaskPhase, NDArray[np.uint8] | None]] = [
        ("tube", "before", masks.tube_before),
        ("tube", "after", masks.tube_after),
        ("gel", "before", masks.gel_before),
        ("gel", "after", masks.gel_after),
    ]
    return [(kind, phase, array) for kind, phase, array in layers if array is not None]


def _directory_of(request: RowAnalysisRequest) -> Path:
    for frame in reversed(request.frames_after):
        if frame.source_path is not None:
            return frame.source_path.parent
    raise MaskArchiveError("原图没有落盘路径，无法确定掩码的存放目录")


def archive_masks(
    request: RowAnalysisRequest, result: RowAnalysisResult, created_at: datetime
) -> list[MediaFile]:
    """把一排的掩码写盘并生成媒体记录。

    只存档该排请求里恰好出现一次的管号（缺的、重复的、多余的管号引擎会另行处理）；
    没有掩码的试管（失败、未找到）跳过。

    Args:
        request: 分析请求，用来确定目录、试验和排号。
        result: 算法结果。
        created_at: 记录时间。

    Returns:
        每个文件一条 ``MediaKind.IMAGE`` 记录，管号升序。

    Raises:
        MaskArchiveError: 无法确定目录、编码失败或写盘失败（已写的文件会删除）。
    """
    wanted = {slot.tube_no for slot in request.tubes}
    counts = Counter(tube.tube_no for tube in result.tubes)
    jobs = [
        (tube.tube_no, layer)
        for tube in sorted(result.tubes, key=lambda t: t.tube_no)
        if tube.masks is not None and tube.tube_no in wanted and counts[tube.tube_no] == 1
        for layer in _layers(tube.masks)
    ]
    if not jobs:
        return []
    directory = _directory_of(request)
    written: list[Path] = []
    records: list[MediaFile] = []
    try:
        for tube_no, (kind, phase, array) in jobs:
            stem = mask_file_name(tube_no, kind, phase).removesuffix(".png")
            path = directory / next_free_name(directory, stem, ".png")
            ok, encoded = cv2.imencode(
                ".png", np.ascontiguousarray(array), [cv2.IMWRITE_PNG_COMPRESSION, _PNG_COMPRESSION]
            )
            if not ok:
                raise MaskArchiveError(f"掩码编码失败：{path.name}")
            data = encoded.tobytes()
            part = Path(f"{path}.part")
            with part.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, path)
            written.append(path)
            records.append(
                MediaFile(
                    file_id=uuid.uuid4().hex,
                    experiment_id=request.experiment_id,
                    kind=MediaKind.IMAGE,
                    path=str(path),
                    sha256=hashlib.sha256(data).hexdigest(),
                    created_at=created_at,
                    row_index=request.row_index,
                    size_bytes=len(data),
                )
            )
    except Exception as exc:
        for path in written:
            path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            for leftover in directory.glob(f"{MASK_PREFIX}*.part"):
                leftover.unlink(missing_ok=True)
        if isinstance(exc, MaskArchiveError):
            raise
        raise MaskArchiveError(f"掩码写盘失败：{exc!r}") from exc
    fsync_directory(directory)
    return records


def read_mask(path: Path) -> NDArray[np.uint8]:
    """读回一张掩码 PNG（路径可以含中文）。

    Args:
        path: 文件路径。

    Returns:
        H×W 的 uint8 数组。

    Raises:
        MaskArchiveError: 文件缺失或无法解码。
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError as exc:
        raise MaskArchiveError(f"无法读取掩码 {path}：{exc}") from exc
    decoded = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise MaskArchiveError(f"无法解码掩码 {path}")
    return np.asarray(decoded, dtype=np.uint8)


def latest_masks(files: Sequence[MediaFile]) -> dict[tuple[int, MaskKind, MaskPhase], MediaFile]:
    """从一个试验的媒体记录里挑出每支试管每种掩码的最新一份。

    Args:
        files: 媒体记录（可以含视频和原图，会被忽略）。

    Returns:
        ``(管号, 种类, 阶段)`` 到记录的映射。重名文件（``_02``）按文件名靠后者为准。
    """
    best: dict[tuple[int, MaskKind, MaskPhase], MediaFile] = {}
    for item in sorted(files, key=lambda f: (f.created_at, Path(f.path).name)):
        if item.kind is not MediaKind.IMAGE:
            continue
        parsed = parse_mask_name(Path(item.path).name)
        if parsed is not None:
            best[parsed] = item
    return best
