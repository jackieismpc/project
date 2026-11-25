"""真实算法 JSON 协议解析器与契约适配层。

支持算法团队产出的 ``result.json``（同时兼容 v2 双帧综合评分与 v1 单帧面积阈值格式），
并处理以下工程细节：
1. 服务端绝对/相对路径自动重定位到本地运行目录；
2. YOLO 独立检出的 ``tube_inst`` 与 ``gel_inst`` 序号并不一致，通过空间包含率与质心距离
   为每个凝胶实例匹配正确的试管掩码；
3. 将任意尺寸的画幅与掩码通过一致的仿射变换对齐到 ``RowAnalysisRequest.image_size``，
   输出严格满足 ``check_row_result`` 契约的 ``TubeAnalysis``。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.interfaces.analysis import (
    GelFeatures,
    StateThresholds,
    TubeAnalysis,
    TubeMasks,
)

Uint8Array = NDArray[np.uint8]


class AlgorithmSummary(BaseModel):
    """算法输出的汇总信息。"""

    model_config = ConfigDict(extra="allow")

    total_images: int = 0
    total_instances: int = 0
    positive: int = 0
    negative: int = 0
    review: int = 0
    score_threshold: float | None = None
    threshold: float | None = None
    buffer: float | None = None


class AlgorithmInstance(BaseModel):
    """算法输出的单支试管实例。"""

    model_config = ConfigDict(extra="allow")

    position: int
    orig_instance: int
    gel_area: float | None = None
    fill_ratio_t0: float | None = None
    height_norm_t2: float | None = None
    curvature_t2: float | None = None
    fill_score: float | None = None
    height_score: float | None = None
    flatness_score: float | None = None
    score: float | None = None
    t2_instance: int | None = None
    prediction: str
    note: str | None = None
    mask_path: str | None = None
    x_center: float | None = None


class AlgorithmImageResult(BaseModel):
    """算法输出的一张整排图片（一对翻转前后图）的检测结果。"""

    model_config = ConfigDict(extra="allow")

    stem: str
    image_path: str | None = None
    t0_image_path: str | None = None
    vis_path: str | None = None
    instances: list[AlgorithmInstance] = Field(default_factory=list)


class AlgorithmRunResult(BaseModel):
    """算法输出的一次完整运行 ``result.json``。"""

    model_config = ConfigDict(extra="allow")

    summary: AlgorithmSummary
    images: list[AlgorithmImageResult] = Field(default_factory=list)
    csv_path: str | None = None
    sorted_csv_path: str | None = None
    output_dir: str | None = None


@dataclass(frozen=True, slots=True)
class LetterboxTransform:
    """等比缩放并居中填充的几何变换（图像与掩码共用）。"""

    src_height: int
    src_width: int
    dst_height: int
    dst_width: int
    scale: float
    pad_x: int
    pad_y: int

    @classmethod
    def fit(cls, src_shape: tuple[int, int], dst_shape: tuple[int, int]) -> LetterboxTransform:
        """计算将 ``(src_h, src_w)`` 放入 ``(dst_h, dst_w)`` 的等比填充变换。"""
        src_h, src_w = src_shape
        dst_h, dst_w = dst_shape
        scale = min(dst_w / max(1, src_w), dst_h / max(1, src_h))
        new_w = max(1, round(src_w * scale))
        new_h = max(1, round(src_h * scale))
        pad_x = (dst_w - new_w) // 2
        pad_y = (dst_h - new_h) // 2
        return cls(
            src_height=src_h,
            src_width=src_w,
            dst_height=dst_h,
            dst_width=dst_w,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )

    def apply_image(self, image: Uint8Array) -> Uint8Array:
        """对 BGR 图像施加相同缩放与居中黑边填充。"""
        if image.shape[:2] == (self.dst_height, self.dst_width):
            return image.copy()
        new_w = max(1, round(self.src_width * self.scale))
        new_h = max(1, round(self.src_height * self.scale))
        interp = cv2.INTER_AREA if self.scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(image, (new_w, new_h), interpolation=interp)
        canvas = np.zeros((self.dst_height, self.dst_width, 3), dtype=np.uint8)
        y1 = min(self.dst_height, self.pad_y + new_h)
        x1 = min(self.dst_width, self.pad_x + new_w)
        canvas[self.pad_y : y1, self.pad_x : x1] = resized[: y1 - self.pad_y, : x1 - self.pad_x]
        return canvas

    def apply_mask(self, mask: Uint8Array) -> Uint8Array:
        """对二值掩码施加最近邻缩放与填充，输出严格为 ``{0, 255}``。"""
        if mask.shape[:2] == (self.dst_height, self.dst_width):
            return np.where(mask > 127, np.uint8(255), np.uint8(0))
        new_w = max(1, round(self.src_width * self.scale))
        new_h = max(1, round(self.src_height * self.scale))
        resized = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((self.dst_height, self.dst_width), dtype=np.uint8)
        y1 = min(self.dst_height, self.pad_y + new_h)
        x1 = min(self.dst_width, self.pad_x + new_w)
        patch = resized[: y1 - self.pad_y, : x1 - self.pad_x]
        canvas[self.pad_y : y1, self.pad_x : x1] = np.where(patch > 127, np.uint8(255), np.uint8(0))
        return canvas


def load_algorithm_run(run_dir: Path) -> AlgorithmRunResult:
    """读取并校验一次算法运行目录下的 ``result.json``。"""
    result_file = run_dir / "result.json"
    raw = json.loads(result_file.read_text(encoding="utf-8"))
    return AlgorithmRunResult.model_validate(raw)


def resolve_run_asset(run_dir: Path, raw_path: str | None, subdir: str) -> Path | None:
    """将算法输出里的路径（可能来自训练服务器）重定位到本地 ``run_dir``。"""
    if not raw_path:
        return None
    name = Path(raw_path).name
    local = run_dir / subdir / name
    if local.is_file():
        return local
    rel = run_dir / raw_path
    if rel.is_file():
        return rel
    direct = Path(raw_path)
    return direct if direct.is_file() else None


def read_binary_mask(path: Path) -> Uint8Array | None:
    """读取掩码 PNG（支持含非 ASCII 路径），归一化为 ``{0, 255}``。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    decoded = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        return None
    return np.where(decoded > 127, np.uint8(255), np.uint8(0))


def _centroid_xy(mask: Uint8Array) -> tuple[float, float] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def match_tube_mask(
    gel_mask: Uint8Array, candidate_tubes: Sequence[Uint8Array]
) -> Uint8Array | None:
    """通过空间包含率与 x 质心距离为凝胶掩码匹配对应的试管掩码。

    算法侧 YOLO 对 ``gel`` 与 ``tube`` 独立编号，``tube_inst{k}`` 与 ``gel_inst{k}``
    不保证一一对应，必须依据几何重叠进行匹配。
    """
    if not candidate_tubes:
        return None
    gel_bool = gel_mask > 0
    gel_area = int(np.count_nonzero(gel_bool))
    gel_center = _centroid_xy(gel_mask)
    if gel_area == 0 or gel_center is None:
        return None

    gx, _ = gel_center
    best_overlap = 0.0
    best_by_overlap: Uint8Array | None = None
    nearest_dist = float("inf")
    nearest_tube: Uint8Array | None = None

    for tube in candidate_tubes:
        if tube.shape != gel_mask.shape:
            continue
        tube_bool = tube > 0
        if not tube_bool.any():
            continue
        inter = int(np.count_nonzero(gel_bool & tube_bool))
        overlap = inter / gel_area
        if overlap > best_overlap:
            best_overlap = overlap
            best_by_overlap = tube
        tube_center = _centroid_xy(tube)
        if tube_center is not None:
            dist = abs(tube_center[0] - gx)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_tube = tube

    if best_by_overlap is not None and best_overlap >= 0.05:
        return best_by_overlap
    max_x_dist = max(40.0, gel_mask.shape[1] * 0.12)
    if nearest_tube is not None and nearest_dist <= max_x_dist:
        return nearest_tube
    return None


def synthesize_fallback_tube_mask(gel_mask: Uint8Array) -> Uint8Array:
    """缺少试管掩码文件时，从凝胶掩码外扩一个竖直胶囊区域作为兜底试管掩码。"""
    ys, xs = np.nonzero(gel_mask)
    out = np.zeros_like(gel_mask, dtype=np.uint8)
    if len(xs) == 0:
        return out
    h, w = gel_mask.shape
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    gw = max(10, x1 - x0)
    gh = max(10, y1 - y0)
    px0 = max(0, x0 - max(4, gw // 6))
    px1 = min(w, x1 + max(4, gw // 6))
    py0 = max(0, y0 - gh)
    py1 = min(h, y1 + gh * 3)
    out[py0:py1, px0:px1] = 255
    return out


def extract_thresholds(
    summary: AlgorithmSummary, *, review_margin: float = 0.01
) -> StateThresholds | None:
    """从 ``summary.score_threshold`` 提取三态判定阈值。"""
    thr = summary.score_threshold
    if thr is None or not 0.0 < thr < 1.0:
        return None
    low = max(0.0, thr - review_margin)
    high = min(1.0, thr + review_margin)
    if low >= high:
        return None
    return StateThresholds(not_gelled_max=low, fully_gelled_min=high)


def _resize_mask_to_shape(
    mask: Uint8Array, target_shape: tuple[int, int], transform: LetterboxTransform | None
) -> Uint8Array:
    if transform is not None:
        transformed = transform.apply_mask(mask)
        if transformed.shape == target_shape:
            return transformed
        return LetterboxTransform.fit(
            (int(transformed.shape[0]), int(transformed.shape[1])), target_shape
        ).apply_mask(transformed)
    if mask.shape == target_shape:
        return np.where(mask > 127, np.uint8(255), np.uint8(0))
    return LetterboxTransform.fit(
        (int(mask.shape[0]), int(mask.shape[1])), target_shape
    ).apply_mask(mask)


def _load_phase_tube_masks(run_dir: Path, stem: str, phase_tag: str | None) -> list[Uint8Array]:
    masks_dir = run_dir / "masks"
    if not masks_dir.is_dir():
        return []
    pattern = (
        f"{stem}_{phase_tag}_tube_inst*.png" if phase_tag is not None else f"{stem}_tube_inst*.png"
    )
    loaded: list[Uint8Array] = []
    for path in sorted(masks_dir.glob(pattern)):
        mask = read_binary_mask(path)
        if mask is not None:
            loaded.append(mask)
    return loaded


def _build_extra_features(
    instance: AlgorithmInstance,
) -> dict[str, float | int | str | bool | None]:
    extra: dict[str, float | int | str | bool | None] = {
        "raw_prediction": instance.prediction,
        "orig_instance": instance.orig_instance,
        "t2_instance": instance.t2_instance,
        "gel_area": instance.gel_area,
        "fill_ratio_t0": instance.fill_ratio_t0,
        "height_norm_t2": instance.height_norm_t2,
        "curvature_t2": instance.curvature_t2,
        "fill_score": instance.fill_score,
        "height_score": instance.height_score,
        "flatness_score": instance.flatness_score,
        "x_center": instance.x_center,
    }
    for k, v in (instance.model_extra or {}).items():
        if isinstance(v, (float, int, str, bool)) or v is None:
            extra[k] = v
    return {k: v for k, v in extra.items() if v is not None}


def convert_instance_to_tube_analysis(
    tube_no: int,
    instance: AlgorithmInstance,
    *,
    run_dir: Path,
    image_result: AlgorithmImageResult,
    target_shape: tuple[int, int],
    transform_before: LetterboxTransform | None = None,
    transform_after: LetterboxTransform | None = None,
    cached_tubes_before: Sequence[Uint8Array] | None = None,
    cached_tubes_after: Sequence[Uint8Array] | None = None,
    score_threshold: float = 0.6,
) -> TubeAnalysis:
    """将算法实例转换为符合 ``GelInspect`` 契约的 ``TubeAnalysis``。"""
    stem = image_result.stem
    is_dual_frame = image_result.t0_image_path is not None or (
        instance.mask_path is not None and "_t0_" in instance.mask_path
    )
    masks_dir = run_dir / "masks"

    gel_before_path: Path | None = None
    if is_dual_frame:
        candidate = masks_dir / f"{stem}_t0_gel_inst{instance.orig_instance}.png"
        gel_before_path = (
            candidate
            if candidate.is_file()
            else resolve_run_asset(run_dir, instance.mask_path, "masks")
        )
    else:
        candidate = masks_dir / f"{stem}_gel_inst{instance.orig_instance}.png"
        gel_before_path = (
            candidate
            if candidate.is_file()
            else resolve_run_asset(run_dir, instance.mask_path, "masks")
        )
    gel_before_raw = read_binary_mask(gel_before_path) if gel_before_path is not None else None

    gel_after_raw: Uint8Array | None = None
    if is_dual_frame:
        if instance.t2_instance is not None:
            t2_path = masks_dir / f"{stem}_t2_gel_inst{instance.t2_instance}.png"
            if t2_path.is_file():
                gel_after_raw = read_binary_mask(t2_path)
    else:
        gel_after_raw = gel_before_raw

    tubes_before_pool = (
        list(cached_tubes_before)
        if cached_tubes_before is not None
        else _load_phase_tube_masks(run_dir, stem, "t0" if is_dual_frame else None)
    )
    tubes_after_pool = (
        list(cached_tubes_after)
        if cached_tubes_after is not None
        else (_load_phase_tube_masks(run_dir, stem, "t2") if is_dual_frame else tubes_before_pool)
    )

    tube_before_raw = (
        match_tube_mask(gel_before_raw, tubes_before_pool) if gel_before_raw is not None else None
    )
    if tube_before_raw is None and gel_before_raw is not None:
        tube_before_raw = synthesize_fallback_tube_mask(gel_before_raw)

    tube_after_raw = (
        match_tube_mask(gel_after_raw, tubes_after_pool) if gel_after_raw is not None else None
    )
    if tube_after_raw is None:
        if gel_after_raw is not None:
            tube_after_raw = synthesize_fallback_tube_mask(gel_after_raw)
        elif tube_before_raw is not None:
            tube_after_raw = tube_before_raw

    missing_before = gel_before_raw is None
    missing_after = gel_after_raw is None

    gel_before = (
        _resize_mask_to_shape(gel_before_raw, target_shape, transform_before)
        if gel_before_raw is not None
        else None
    )
    gel_after = (
        _resize_mask_to_shape(gel_after_raw, target_shape, transform_after)
        if gel_after_raw is not None
        else None
    )
    if gel_before is not None and not gel_before.any():
        gel_before = None
        missing_before = True
    if gel_after is not None and not gel_after.any():
        gel_after = None
        missing_after = True

    tube_before = (
        _resize_mask_to_shape(tube_before_raw, target_shape, transform_before)
        if tube_before_raw is not None
        else np.zeros(target_shape, dtype=np.uint8)
    )
    tube_after = (
        _resize_mask_to_shape(tube_after_raw, target_shape, transform_after)
        if tube_after_raw is not None
        else tube_before
    )

    extra = _build_extra_features(instance)
    score = min(1.0, max(0.0, float(instance.score))) if instance.score is not None else None
    pred = instance.prediction.strip().lower()

    if missing_before and missing_after:
        return TubeAnalysis(
            tube_no=tube_no,
            status=TubeAnalysisStatus.TUBE_NOT_FOUND,
            message=instance.note or "未检测到试管与凝胶掩码",
            features=GelFeatures(extra=extra),
        )

    area_before = float(np.count_nonzero(gel_before)) if gel_before is not None else None
    area_after = float(np.count_nonzero(gel_after)) if gel_after is not None else None
    centroid_before = _centroid_xy(gel_before) if gel_before is not None else None
    centroid_after = _centroid_xy(gel_after) if gel_after is not None else None

    masks = TubeMasks(
        tube_before=tube_before,
        tube_after=tube_after,
        gel_before=None if missing_before else gel_before,
        gel_after=None if missing_after else gel_after,
    )

    if missing_before or missing_after:
        state_if_known = (
            GelState.NOT_GELLED
            if pred == "negative"
            else (GelState.FULLY_GELLED if pred == "positive" else None)
        )
        return TubeAnalysis(
            tube_no=tube_no,
            status=TubeAnalysisStatus.GEL_MISSING,
            state=state_if_known,
            score=score,
            features=GelFeatures(
                area_before=None if missing_before else area_before,
                area_after=None if missing_after else area_after,
                centroid_before=None if missing_before else centroid_before,
                centroid_after=None if missing_after else centroid_after,
                gel_missing_before=missing_before,
                gel_missing_after=missing_after,
                extra=extra,
            ),
            masks=masks,
            message=instance.note,
        )

    assert area_before is not None and area_after is not None
    delta_area = abs(area_after - area_before) / area_before * 100.0 if area_before > 0 else 0.0
    features = GelFeatures(
        area_before=area_before,
        area_after=area_after,
        delta_area_pct=delta_area,
        centroid_before=centroid_before,
        centroid_after=centroid_after,
        extra=extra,
    )

    if pred == "positive":
        state: GelState | None = GelState.FULLY_GELLED
        status = TubeAnalysisStatus.OK
    elif pred == "negative":
        state = GelState.NOT_GELLED
        status = TubeAnalysisStatus.OK
    elif pred == "review":
        # 兼容历史离线样本中的 review 标记：按评分阈值归并为二分类，不再输出待复核。
        if score is not None and score >= score_threshold:
            state = GelState.FULLY_GELLED
        else:
            state = GelState.NOT_GELLED
        status = TubeAnalysisStatus.OK
    else:
        return TubeAnalysis(
            tube_no=tube_no,
            status=TubeAnalysisStatus.LOW_QUALITY,
            state=None,
            score=score,
            features=features,
            masks=masks,
            message=instance.note or f"未知算法预测：{instance.prediction}",
        )

    return TubeAnalysis(
        tube_no=tube_no,
        status=status,
        state=state,
        score=score,
        features=features,
        masks=masks,
        message=None,
    )
