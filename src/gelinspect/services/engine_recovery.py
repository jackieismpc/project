"""软件重启后重试分析：从入库的媒体记录判断哪些排还能分析。

进程退出后调度和内存里的帧都没了，但原图、哈希、抓帧时间和翻转角度都在库里，所以已经抓帧、
还没有结果的排可以离线重新分析，不碰设备，也不再翻转。没有抓帧的排错过了读数时间，不能续读。
抓帧时间是 P10 才入库的，更早的记录没有它，这些排不能重试，界面会说明。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from gelinspect.domain.entities import Experiment
from gelinspect.domain.plate import tube_to_position
from gelinspect.interfaces.analysis import MIN_FRAMES_PER_SIDE
from gelinspect.interfaces.storage import MediaFile
from gelinspect.services.video.media import latest_raw_images
from gelinspect.services.video.snapshot import SavedImage


@dataclass(frozen=True, slots=True)
class RowRecovery:
    """一排重试分析需要的内容。

    Attributes:
        files: 翻转前后的原图。
        flip_angle_deg: 这次翻转的实际角度。
    """

    files: tuple[SavedImage, ...]
    flip_angle_deg: float | None


def _saved(item: MediaFile, phase: str, index: int) -> SavedImage | None:
    if item.captured_at is None:
        return None
    return SavedImage(
        phase=cast("Literal['before', 'after']", phase),
        index=index,
        path=Path(item.path),
        sha256=item.sha256,
        size_bytes=item.size_bytes or 0,
        sequence=0,
        captured_at=item.captured_at,
    )


def row_recovery(files: Sequence[MediaFile]) -> RowRecovery | None:
    """由一排的媒体记录得出重试内容。

    Args:
        files: 这一排的媒体记录。

    Returns:
        原图齐全且有抓帧时间时返回内容，否则为 None。
    """
    raw = latest_raw_images(files)
    saved: list[SavedImage] = []
    for phase, items in raw.items():
        if len(items) < MIN_FRAMES_PER_SIDE:
            return None
        for index, item in enumerate(items, start=1):
            image = _saved(item, phase, index)
            if image is None:
                return None
            saved.append(image)
    return RowRecovery(tuple(saved), raw["after"][-1].flip_angle_deg)


def recovery_plan(experiment: Experiment, media: Sequence[MediaFile]) -> Mapping[int, RowRecovery]:
    """找出还能离线重新分析的排：已抓帧、原图齐全、还没有结果。

    Args:
        experiment: 试验。
        media: 这个试验的媒体记录。

    Returns:
        排号到重试内容。
    """
    tubes_by_row: dict[int, list[int]] = {}
    for assignment in experiment.assignments:
        row_index = tube_to_position(assignment.tube_no).row_index
        tubes_by_row.setdefault(row_index, []).append(assignment.tube_no)
    plan: dict[int, RowRecovery] = {}
    for row_index, tubes in sorted(tubes_by_row.items()):
        if all(tube in experiment.results for tube in tubes):
            continue
        recovery = row_recovery([m for m in media if m.row_index == row_index])
        if recovery is not None:
            plan[row_index] = recovery
    return plan
