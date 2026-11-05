"""把录制和抓帧的结果转换为 ``interfaces.storage.MediaFile``。

管线本身不访问数据库。试验引擎在自己的线程里拿到 :class:`SegmentRecord` 或
:class:`SnapshotResult` 后，用这里的函数转换，再交给 ``MediaRepository.add``，
这样仓储的线程归属由引擎决定。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from gelinspect.interfaces.storage import MediaFile, MediaKind
from gelinspect.services.video.recorder import SegmentRecord
from gelinspect.services.video.snapshot import SnapshotResult

RAW_IMAGE_NAME = re.compile(
    r"^(?P<phase>before|after)_(?P<index>\d+)\.(?:png|jpg|jpeg)$", re.IGNORECASE
)
"""原图的文件名：翻转前后各自的序号。"""


def segment_to_media_file(
    record: SegmentRecord, *, created_at: datetime | None = None
) -> MediaFile:
    """把录制段记录转换为媒体文件记录。

    Args:
        record: 录制段记录。
        created_at: 记录时间，默认当前 UTC 时间。

    Returns:
        ``kind=VIDEO`` 的媒体文件记录。
    """
    return MediaFile(
        file_id=uuid.uuid4().hex,
        experiment_id=record.experiment_id,
        kind=MediaKind.VIDEO,
        path=str(record.path),
        sha256=record.sha256,
        created_at=created_at or datetime.now(UTC),
        row_index=record.row_index,
        size_bytes=record.size_bytes,
    )


def snapshot_to_media_files(
    result: SnapshotResult,
    *,
    created_at: datetime | None = None,
    flip_angle_deg: float | None = None,
) -> list[MediaFile]:
    """把抓帧结果转换为每张图一条的媒体文件记录。

    Args:
        result: 抓帧结果。
        created_at: 记录时间，默认当前 UTC 时间。
        flip_angle_deg: 这次翻转的实际角度。

    Returns:
        ``kind=IMAGE`` 的媒体文件记录，顺序同 ``result.files``。
    """
    stamp = created_at or datetime.now(UTC)
    return [
        MediaFile(
            file_id=uuid.uuid4().hex,
            experiment_id=result.experiment_id,
            kind=MediaKind.IMAGE,
            path=str(item.path),
            sha256=item.sha256,
            created_at=stamp,
            row_index=result.row_index,
            size_bytes=item.size_bytes,
            captured_at=item.captured_at,
            flip_angle_deg=flip_angle_deg,
        )
        for item in result.files
    ]


def latest_raw_images(files: Sequence[MediaFile]) -> dict[str, list[MediaFile]]:
    """一排最新一次抓帧的原图，按序号升序（掩码等其他图像不算）。

    Args:
        files: 这一排的媒体记录。

    Returns:
        ``{"before": [...], "after": [...]}``。
    """
    by_dir: dict[Path, list[tuple[str, int, MediaFile]]] = {}
    for item in files:
        if item.kind is not MediaKind.IMAGE:
            continue
        match = RAW_IMAGE_NAME.match(Path(item.path).name)
        if match is None:
            continue
        by_dir.setdefault(Path(item.path).parent, []).append(
            (match["phase"].lower(), int(match["index"]), item)
        )
    result: dict[str, list[MediaFile]] = {"before": [], "after": []}
    if not by_dir:
        return result
    newest = max(by_dir, key=lambda d: (max(i.created_at for _, _, i in by_dir[d]), d.name))
    for phase, _, item in sorted(by_dir[newest], key=lambda e: (e[0], e[1])):
        result[phase].append(item)
    return result
