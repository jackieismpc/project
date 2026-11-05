"""界面查看原图、掩码和录像的服务：读文件、核对哈希、裁剪出单管、提取掩码轮廓。

解码大图（12 MP 的 PNG）要几百毫秒，所以这里的方法都是同步的、与线程无关的纯函数式调用，
界面把它们放到后台线程里执行（见 ``ui/background.py``）。每个入口先校验 ``experiment_view``。

* **原图和掩码**：入库时记录了 SHA-256，读出来先核对。哈希不符的文件仍然显示（让人看到问题），
  但在结果里带上 :attr:`ImageWarning.HASH_MISMATCH`，界面必须提示。
* **裁剪**：整排大图里按试管掩码的外接框裁出本管；没有掩码（算法失败）时按孔号把整幅图
  切成 8 列取对应的一列。
* **轮廓**：掩码在缩小后的裁剪图上提取轮廓，界面用矢量线条叠加，缩放不失真。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import HOLES_PER_ROW, tube_to_position
from gelinspect.interfaces.storage import MediaFile, MediaKind, MediaRepository
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.mask_archive import MaskKind, MaskPhase, latest_masks
from gelinspect.services.replay import ReplayError, VideoClip
from gelinspect.services.video.media import RAW_IMAGE_NAME, latest_raw_images
from gelinspect.services.video.preview import resize_to_long_side

CROP_LONG_SIDE = 520
"""单管裁剪图的长边上限。"""
SEQUENCE_LONG_SIDE = 220
"""3 帧序列缩略图的长边上限。"""
MIN_SIDE_PADDING = 0.06
"""裁剪框短边外扩量的下限，相对长边。"""

Image = NDArray[np.uint8]
Polygons = tuple[NDArray[np.int32], ...]


class ImageWarning(StrEnum):
    """读图时发现的问题，界面据此提示，不能悄悄忽略。"""

    HASH_MISMATCH = "hash_mismatch"
    """文件内容与入库的 SHA-256 不符，可能已被改动。"""
    FILE_MISSING = "file_missing"
    """入库的文件不存在或无法解码。"""
    NO_MASKS = "no_masks"
    """这支试管没有存档的掩码（算法失败，或旧数据），裁剪按孔位切分。"""


class MediaStorage(Protocol):
    """媒体服务需要的存储。"""

    @property
    def media(self) -> MediaRepository:
        """媒体文件记录仓储。"""
        ...


@dataclass(frozen=True, slots=True)
class TubeSideImage:
    """单管翻转前或翻转后的一张图。

    Attributes:
        image: BGR 裁剪图（已缩小）。
        gel: 凝胶掩码轮廓（裁剪图坐标），可能有多个，带孔的轮廓用奇偶填充。
        tube: 试管掩码轮廓。
        source_name: 原图文件名。
        recorded_at: 该排抓帧入库的时间。
    """

    image: Image
    gel: Polygons
    tube: Polygons
    source_name: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class TubeImages:
    """单管详情要显示的全部图像。

    Attributes:
        experiment_id: 试验标识。
        tube_no: 管号。
        row_index: 排号。
        before: 翻转前代表帧（该排最后一张翻转前原图）。
        after: 翻转后代表帧。
        sequence_before: 翻转前各帧的缩略图（同一个裁剪框）。
        sequence_after: 翻转后各帧的缩略图。
        warnings: 读图时发现的问题。
    """

    experiment_id: str
    tube_no: int
    row_index: int
    before: TubeSideImage | None
    after: TubeSideImage | None
    sequence_before: tuple[Image, ...] = ()
    sequence_after: tuple[Image, ...] = ()
    warnings: tuple[ImageWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class RowThumbnails:
    """一排整幅原图的缩略图（图像显示标签的 8 连图）。

    Attributes:
        row_index: 排号。
        before: 翻转前代表帧。
        after: 翻转后代表帧。
        warnings: 读图时发现的问题。
    """

    row_index: int
    before: Image | None
    after: Image | None
    warnings: tuple[ImageWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class Recording:
    """一排的录像记录。

    Attributes:
        row_index: 排号。
        path: MP4 路径。
        sha256: 入库的哈希。
        size_bytes: 文件大小。
    """

    row_index: int
    path: Path
    sha256: str
    size_bytes: int | None


@dataclass(slots=True)
class _Reader:
    """带告警收集的读图器。"""

    warnings: list[ImageWarning] = field(default_factory=list)

    def warn(self, warning: ImageWarning) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)

    def read(self, item: MediaFile, flags: int) -> Image | None:
        try:
            data = np.fromfile(item.path, dtype=np.uint8)
        except OSError:
            self.warn(ImageWarning.FILE_MISSING)
            return None
        if hashlib.sha256(data.tobytes()).hexdigest() != item.sha256:
            self.warn(ImageWarning.HASH_MISMATCH)
        decoded = cv2.imdecode(data, flags)
        if decoded is None:
            self.warn(ImageWarning.FILE_MISSING)
            return None
        return np.asarray(decoded, dtype=np.uint8)


def _bounding_box(mask: Image, margin: float = 0.18) -> tuple[int, int, int, int] | None:
    """掩码的外接框，向外扩一圈。返回 ``(x0, y0, x1, y1)``，掩码为空时为 None。"""
    points = cv2.findNonZero(mask)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    # 按各自方向外扩：都按长边外扩的话，竖长的试管两侧会留下大片背景。
    # 短边至少外扩长边的 6%，管壁外留一点背景，掩码轮廓才看得清。
    pad_x, pad_y = (round(max(margin * side, MIN_SIDE_PADDING * max(w, h))) for side in (w, h))
    height, width = mask.shape[:2]
    return (
        max(0, x - pad_x),
        max(0, y - pad_y),
        min(width, x + w + pad_x),
        min(height, y + h + pad_y),
    )


def _column_box(shape: tuple[int, ...], position: int) -> tuple[int, int, int, int]:
    """没有掩码时的后备裁剪：把整幅图切成 8 列取第 ``position`` 列。"""
    height, width = shape[:2]
    step = width / HOLES_PER_ROW
    return round((position - 1) * step), 0, round(position * step), height


def _contours(mask: Image) -> Polygons:
    found, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polygons: list[NDArray[np.int32]] = []
    for contour in found:
        if len(contour) < 3:
            continue
        approx = cv2.approxPolyDP(contour, 0.8, True)
        polygons.append(np.asarray(approx, dtype=np.int32).reshape(-1, 2))
    return tuple(polygons)


def _crop(
    image: Image, box: tuple[int, int, int, int], long_side: int
) -> tuple[Image, float, tuple[int, int, int, int]]:
    x0, y0, x1, y1 = box
    crop = image[y0:y1, x0:x1]
    small = resize_to_long_side(np.ascontiguousarray(crop), long_side)
    scale = small.shape[1] / max(1, crop.shape[1])
    return small, scale, box


def _crop_mask(mask: Image | None, box: tuple[int, int, int, int], size: tuple[int, int]) -> Image:
    """裁剪掩码并缩放到图像大小（最近邻，保持二值）。"""
    x0, y0, x1, y1 = box
    if mask is None:
        return np.zeros((size[1], size[0]), dtype=np.uint8)
    crop = np.ascontiguousarray(mask[y0:y1, x0:x1])
    resized = cv2.resize(crop, size, interpolation=cv2.INTER_NEAREST)
    return np.asarray(resized, dtype=np.uint8)


class MediaService:
    """读取原图、掩码和录像，供监控页和单管详情使用。"""

    def __init__(self, auth: AuthService, storage: MediaStorage) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
        """
        self._auth = auth
        self._storage = storage

    # ------------------------------------------------------------------ 录像

    def recordings(self, session: Session | None, experiment_id: str) -> dict[int, Recording]:
        """试验里每一排最新的录像。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。

        Returns:
            排号到录像的映射。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        found: dict[int, Recording] = {}
        for item in self._storage.media.list_for_experiment(experiment_id, kind=MediaKind.VIDEO):
            if item.row_index is not None:
                found[item.row_index] = Recording(
                    item.row_index, Path(item.path), item.sha256, item.size_bytes
                )
        return found

    def open_clip(self, session: Session | None, experiment_id: str, row_index: int) -> VideoClip:
        """打开一排的录像用于回放，打开前核对哈希。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。
            row_index: 排号。

        Returns:
            录像，用完调用 ``close``。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 这一排没有录像（``MEDIA_MISSING``），或文件被改动、无法解码
                （``MEDIA_TAMPERED`` / ``MEDIA_MISSING``）。
        """
        recording = self.recordings(session, experiment_id).get(row_index)
        if recording is None:
            raise ServiceError(
                f"第 {row_index} 排没有录像", ServiceErrorCode.MEDIA_MISSING, row=row_index
            )
        try:
            return VideoClip(recording.path, sha256=recording.sha256)
        except ReplayError as exc:
            code = (
                ServiceErrorCode.MEDIA_TAMPERED
                if "SHA-256" in str(exc)
                else ServiceErrorCode.MEDIA_MISSING
            )
            raise ServiceError(str(exc), code, row=row_index) from exc

    # ------------------------------------------------------------------ 图像

    def rows_with_images(self, session: Session | None, experiment_id: str) -> list[int]:
        """有抓拍原图的排号。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。

        Returns:
            升序排号。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        files = self._storage.media.list_for_experiment(experiment_id, kind=MediaKind.IMAGE)
        rows = {
            i.row_index
            for i in files
            if i.row_index is not None and RAW_IMAGE_NAME.match(Path(i.path).name)
        }
        return sorted(rows)

    def row_thumbnails(
        self,
        session: Session | None,
        experiment_id: str,
        row_index: int,
        long_side: int = 640,
    ) -> RowThumbnails:
        """一排整幅原图的缩略图：翻转前后的代表帧（各自最后一张）。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。
            row_index: 排号。
            long_side: 缩略图长边。

        Returns:
            缩略图；这一排没有原图时两张都是 None。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        files = self._storage.media.list_for_experiment(
            experiment_id, kind=MediaKind.IMAGE, row_index=row_index
        )
        raw = latest_raw_images(files)
        reader = _Reader()
        sides: dict[str, Image | None] = {}
        for phase in ("before", "after"):
            image = reader.read(raw[phase][-1], cv2.IMREAD_COLOR) if raw[phase] else None
            sides[phase] = None if image is None else resize_to_long_side(image, long_side)
        return RowThumbnails(row_index, sides["before"], sides["after"], tuple(reader.warnings))

    def tube_images(self, session: Session | None, experiment_id: str, tube_no: int) -> TubeImages:
        """单管详情的图像：翻转前后的代表帧裁剪、掩码轮廓和 3 帧序列。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。
            tube_no: 管号。

        Returns:
            图像集合；缺的图为 None，原因在 ``warnings``。

        Raises:
            PermissionDeniedError: 没有权限。
            InvalidPlatePositionError: 管号超出范围。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        position = tube_to_position(tube_no)
        files = self._storage.media.list_for_experiment(
            experiment_id, kind=MediaKind.IMAGE, row_index=position.row_index
        )
        raw = latest_raw_images(files)
        masks = {k: v for k, v in latest_masks(files).items() if k[0] == tube_no}
        reader = _Reader()
        if not any(key[1] == "tube" for key in masks):
            reader.warn(ImageWarning.NO_MASKS)

        def mask_of(kind: MaskKind, phase: MaskPhase) -> Image | None:
            item = masks.get((tube_no, kind, phase))
            return None if item is None else reader.read(item, cv2.IMREAD_GRAYSCALE)

        sides: dict[str, TubeSideImage | None] = {}
        boxes: dict[str, tuple[int, int, int, int] | None] = {}
        for phase_name in ("before", "after"):
            phase: MaskPhase = "before" if phase_name == "before" else "after"
            frames = raw[phase_name]
            if not frames:
                sides[phase_name] = None
                boxes[phase_name] = None
                continue
            image = reader.read(frames[-1], cv2.IMREAD_COLOR)
            if image is None:
                sides[phase_name] = None
                boxes[phase_name] = None
                continue
            tube_mask = mask_of("tube", phase)
            gel_mask = mask_of("gel", phase)
            found = _bounding_box(tube_mask) if tube_mask is not None else None
            box = found or _column_box(image.shape, position.hole_index)
            crop, _, _ = _crop(image, box, CROP_LONG_SIDE)
            size = (crop.shape[1], crop.shape[0])
            sides[phase_name] = TubeSideImage(
                image=crop,
                gel=_contours(_crop_mask(gel_mask, box, size)) if gel_mask is not None else (),
                tube=_contours(_crop_mask(tube_mask, box, size)) if tube_mask is not None else (),
                source_name=Path(frames[-1].path).name,
                recorded_at=frames[-1].created_at,
            )
            boxes[phase_name] = box

        sequences: dict[str, list[Image]] = {"before": [], "after": []}
        for phase_name in ("before", "after"):
            seq_box = boxes[phase_name]
            if seq_box is None:
                continue
            box = seq_box
            for item in raw[phase_name]:
                frame = reader.read(item, cv2.IMREAD_COLOR)
                if frame is not None:
                    thumb, _, _ = _crop(frame, box, SEQUENCE_LONG_SIDE)
                    sequences[phase_name].append(thumb)
        return TubeImages(
            experiment_id=experiment_id,
            tube_no=tube_no,
            row_index=position.row_index,
            before=sides["before"],
            after=sides["after"],
            sequence_before=tuple(sequences["before"]),
            sequence_after=tuple(sequences["after"]),
            warnings=tuple(reader.warnings),
        )


__all__ = [
    "CROP_LONG_SIDE",
    "ImageWarning",
    "MediaService",
    "Recording",
    "RowThumbnails",
    "TubeImages",
    "TubeSideImage",
]
