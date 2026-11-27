"""正式 YOLOv8-Seg ONNX 算法后端（实现 :class:`~gelinspect.interfaces.analysis.GelAnalyzer`）。

加载 ``ckpt/gel-seg-v1.onnx``（或配置/环境变量指定的 ONNX 权重），执行试管与凝胶实例分割，
结合 ``gel_inspection`` 特征评分规则输出严格满足软件契约的 ``RowAnalysisResult``。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.infrastructure.analysis.algorithm_json import match_tube_mask
from gelinspect.infrastructure.analysis.gel_inspection_core import (
    FeatureScoringConfig,
    FrameSegmentation,
    OnnxGelSegmenter,
    SegmentConfig,
    SegmentedInstance,
    evaluate_gel_pair,
)
from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    GelFeatures,
    RowAnalysisRequest,
    RowAnalysisResult,
    StateThresholds,
    TubeAnalysis,
    TubeMasks,
    TubeSlot,
)
from gelinspect.services.runtime_paths import app_root

_log = logging.getLogger(__name__)

MODEL_PATH_ENV = "GEL_MODEL_PATH"
"""覆盖 ONNX 权重路径的环境变量。"""

DEFAULT_MODEL_PATH = Path("ckpt") / "gel-seg-v1.onnx"
"""默认 ONNX 权重相对路径。"""

FramePairMode = Literal["post_flip_span", "before_after"]
SlotMappingMode = Literal["order", "grid"]
Uint8Array = NDArray[np.uint8]


class YoloOnnxConfig(BaseModel):
    """``YoloOnnxAnalyzer`` 配置模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_path: str = str(DEFAULT_MODEL_PATH)
    model_version: str | None = None
    config_version: str | None = None
    frame_pair_mode: FramePairMode = "post_flip_span"
    slot_mapping: SlotMappingMode = "order"
    reverse_x: bool = False
    roi_x_range: tuple[float, float] | None = None
    slot_x_centers: list[float] | None = None
    segment: SegmentConfig = SegmentConfig()
    scoring: FeatureScoringConfig = FeatureScoringConfig()

    @model_validator(mode="after")
    def _validate_layout(self) -> YoloOnnxConfig:
        if self.slot_x_centers is not None and len(self.slot_x_centers) != 8:
            raise ValueError("slot_x_centers 必须包含 8 个孔位中心 x 坐标")
        if self.roi_x_range is not None and self.roi_x_range[0] >= self.roi_x_range[1]:
            raise ValueError("roi_x_range 必须满足 x_min < x_max")
        return self


@dataclass(frozen=True, slots=True)
class _SlotCandidate:
    x_center: float
    tube: SegmentedInstance | None
    gel: SegmentedInstance | None


@dataclass(frozen=True, slots=True)
class _PairedTube:
    x_center: float
    tube_t0: SegmentedInstance | None
    gel_t0: SegmentedInstance | None
    tube_t2: SegmentedInstance | None
    gel_t2: SegmentedInstance | None


def resolve_model_path(raw: str | Path | None, config_path: Path | None) -> Path:
    """按优先级解析 ONNX 模型路径：环境变量 ``GEL_MODEL_PATH`` > YAML/显式路径 > 默认路径。"""
    env_path = os.environ.get(MODEL_PATH_ENV, "").strip()
    candidate = Path(env_path) if env_path else Path(raw or DEFAULT_MODEL_PATH)
    if candidate.is_absolute():
        return candidate
    if config_path is not None:
        by_config = config_path.resolve().parent / candidate
        if by_config.is_file():
            return by_config
        by_parent = config_path.resolve().parent.parent / candidate
        if by_parent.is_file():
            return by_parent
    by_root = app_root() / candidate
    return by_root


def _short_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest()[:8]
    except OSError:
        return None


def _centroid_xy(mask: Uint8Array) -> tuple[float, float] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def _is_constant_image(image: Uint8Array) -> bool:
    return bool(image.size == 0 or int(image.min()) == int(image.max()))


def _build_candidates(seg: FrameSegmentation, *, reverse_x: bool) -> list[_SlotCandidate]:
    """将单帧内独立检出的凝胶与试管实例按空间包含率关联成候选管位。"""
    tube_masks = [t.mask for t in seg.tubes]
    tube_by_id: dict[int, SegmentedInstance] = {id(t.mask): t for t in seg.tubes}
    matched_gels_by_tube: dict[int, list[SegmentedInstance]] = {}
    unmatched_gels: list[SegmentedInstance] = []

    for gel in seg.gels:
        matched_mask = match_tube_mask(gel.mask, tube_masks)
        if matched_mask is not None and id(matched_mask) in tube_by_id:
            matched_gels_by_tube.setdefault(id(matched_mask), []).append(gel)
        else:
            unmatched_gels.append(gel)

    candidates: list[_SlotCandidate] = []
    for tube in seg.tubes:
        gels = matched_gels_by_tube.get(id(tube.mask), [])
        best_gel = max(gels, key=lambda g: (g.conf, g.area)) if gels else None
        candidates.append(_SlotCandidate(x_center=tube.x_center, tube=tube, gel=best_gel))
    for gel in unmatched_gels:
        candidates.append(_SlotCandidate(x_center=gel.x_center, tube=None, gel=gel))

    candidates.sort(key=lambda item: item.x_center, reverse=reverse_x)
    return candidates


def _pair_candidates(
    c0: Sequence[_SlotCandidate],
    c2: Sequence[_SlotCandidate],
    *,
    max_x_dist: float,
    reverse_x: bool,
) -> list[_PairedTube]:
    """按横向距离贪心将 t0 与 t2 候选管位一一配对。"""
    if not c0 and not c2:
        return []
    pairs: list[tuple[float, int, int]] = []
    for i, left in enumerate(c0):
        for j, right in enumerate(c2):
            dist = abs(left.x_center - right.x_center)
            if dist <= max_x_dist:
                pairs.append((dist, i, j))
    pairs.sort(key=lambda item: item[0])

    used_0: set[int] = set()
    used_2: set[int] = set()
    matched_map: dict[int, int] = {}
    for _, i, j in pairs:
        if i in used_0 or j in used_2:
            continue
        used_0.add(i)
        used_2.add(j)
        matched_map[i] = j

    paired: list[_PairedTube] = []
    for i, left in enumerate(c0):
        j_opt = matched_map.get(i)
        right_opt: _SlotCandidate | None = c2[j_opt] if j_opt is not None else None
        paired.append(
            _PairedTube(
                x_center=left.x_center,
                tube_t0=left.tube,
                gel_t0=left.gel,
                tube_t2=None if right_opt is None else right_opt.tube,
                gel_t2=None if right_opt is None else right_opt.gel,
            )
        )
    for j, right in enumerate(c2):
        if j not in used_2:
            paired.append(
                _PairedTube(
                    x_center=right.x_center,
                    tube_t0=None,
                    gel_t0=None,
                    tube_t2=right.tube,
                    gel_t2=right.gel,
                )
            )
    paired.sort(key=lambda item: item.x_center, reverse=reverse_x)
    return paired


def _assign_to_slots(
    slots: Sequence[TubeSlot],
    paired: Sequence[_PairedTube],
    *,
    image_width: int,
    cfg: YoloOnnxConfig,
) -> dict[int, _PairedTube | None]:
    """将配对结果映射到请求的试管管号。"""
    slots_sorted = sorted(slots, key=lambda s: s.position_in_row)
    if cfg.slot_mapping == "order":
        return {
            slot.tube_no: paired[idx] if idx < len(paired) else None
            for idx, slot in enumerate(slots_sorted)
        }

    # grid 模式：按校准中心点或 ROI 8 等分映射孔位 1..8
    if cfg.slot_x_centers is not None and len(cfg.slot_x_centers) == 8:
        centers = list(cfg.slot_x_centers)
    else:
        x_min, x_max = cfg.roi_x_range if cfg.roi_x_range is not None else (0.0, float(image_width))
        step = max(1.0, (x_max - x_min) / 8.0)
        centers = [x_min + (idx + 0.5) * step for idx in range(8)]
    if cfg.reverse_x:
        centers = list(reversed(centers))

    by_hole: dict[int, _PairedTube] = {}
    best_dist: dict[int, float] = {}
    for cand in paired:
        hole = min(range(1, 9), key=lambda h: abs(cand.x_center - centers[h - 1]))
        dist = abs(cand.x_center - centers[hole - 1])
        if hole not in by_hole or dist < best_dist[hole]:
            by_hole[hole] = cand
            best_dist[hole] = dist
    return {slot.tube_no: by_hole.get(slot.position_in_row) for slot in slots_sorted}


class YoloOnnxAnalyzer:
    """``GelAnalyzer`` 的 YOLOv8-Seg ONNX 实现，可无参构造。"""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        device: int | str | None = None,
        frame_pair_mode: FramePairMode | None = None,
        segmenter: OnnxGelSegmenter | None = None,
    ) -> None:
        """创建分析器实例，构造时不加载模型也不导入 ``onnxruntime``。"""
        self._explicit_model_path = None if model_path is None else str(model_path)
        self._explicit_device = device
        self._explicit_frame_pair_mode = frame_pair_mode
        self._segmenter = segmenter or OnnxGelSegmenter()
        self._config = YoloOnnxConfig()
        self._info: AnalyzerInfo | None = None

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """读取配置、加载 ONNX 模型并完成预热。"""
        raw_cfg: dict[str, object] = {}
        if config_path is not None:
            try:
                loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                raise AnalyzerError(f"无法读取算法配置文件 {config_path}：{exc}") from exc
            if not isinstance(loaded, dict):
                raise AnalyzerError(f"算法配置文件 {config_path} 顶层必须是字典")
            raw_cfg = loaded

        try:
            cfg = YoloOnnxConfig.model_validate(raw_cfg)
        except ValidationError as exc:
            raise AnalyzerError(f"算法配置无效：{exc}") from exc

        if self._explicit_model_path is not None:
            cfg = cfg.model_copy(update={"model_path": self._explicit_model_path})
        if self._explicit_device is not None:
            cfg = cfg.model_copy(
                update={"segment": cfg.segment.model_copy(update={"device": self._explicit_device})}
            )
        if self._explicit_frame_pair_mode is not None:
            cfg = cfg.model_copy(update={"frame_pair_mode": self._explicit_frame_pair_mode})

        model_path = resolve_model_path(cfg.model_path, config_path)
        try:
            self._segmenter.load(model_path, cfg.segment)
        except Exception as exc:
            self._segmenter.close()
            self._info = None
            raise AnalyzerError(f"加载 ONNX 模型失败（{model_path}）：{exc}") from exc

        short_hash = _short_sha256(model_path)
        model_version = cfg.model_version or (
            f"{model_path.stem}-{short_hash}" if short_hash else model_path.stem
        )
        config_version = cfg.config_version or (
            config_path.stem if config_path is not None else "yolo-onnx-default"
        )
        thr = cfg.scoring.score_threshold
        buf = cfg.scoring.score_review_buffer
        low = max(0.0, thr - buf)
        high = min(1.0, thr + buf)
        thresholds = (
            StateThresholds(not_gelled_max=low, fully_gelled_min=high) if low < high else None
        )

        self._config = cfg
        self._info = AnalyzerInfo(
            model_version=model_version,
            config_version=config_version,
            thresholds=thresholds,
        )
        return self._info

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """同步分析一排试管，严格满足 ``GelAnalyzer`` 契约。"""
        if self._info is None or not self._segmenter.is_loaded:
            raise AnalyzerError("ONNX 算法模型未加载或已关闭")

        started = time.perf_counter()
        cfg = self._config
        if cfg.frame_pair_mode == "post_flip_span":
            t0_frame = request.frames_after[0]
            t2_frame = request.frames_after[-1]
        else:
            t0_frame = request.frames_before[-1]
            t2_frame = request.frames_after[-1]

        t0_img = t0_frame.image
        t2_img = t2_frame.image
        if _is_constant_image(t0_img) or _is_constant_image(t2_img):
            tubes = [
                TubeAnalysis(
                    tube_no=slot.tube_no,
                    status=TubeAnalysisStatus.LOW_QUALITY,
                    message="图像无有效内容（全黑或全白）",
                )
                for slot in request.tubes
            ]
            return RowAnalysisResult(
                analyzer_info=self._info,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                tubes=tubes,
            )

        try:
            seg_t0 = self._segmenter.segment(t0_img, cfg.segment)
            seg_t2 = self._segmenter.segment(t2_img, cfg.segment)
        except Exception as exc:
            raise AnalyzerError(f"ONNX 推理失败：{exc}") from exc

        c0 = _build_candidates(seg_t0, reverse_x=cfg.reverse_x)
        c2 = _build_candidates(seg_t2, reverse_x=cfg.reverse_x)
        paired = _pair_candidates(
            c0, c2, max_x_dist=cfg.scoring.max_x_dist, reverse_x=cfg.reverse_x
        )
        slot_map = _assign_to_slots(
            request.tubes, paired, image_width=request.image_size[1], cfg=cfg
        )

        span_s = round((t2_frame.captured_at - t0_frame.captured_at).total_seconds(), 3)
        if cfg.frame_pair_mode == "post_flip_span" and span_s < 0.2:
            _log.warning(
                "第 %d 排 t0/t2 时间间隔仅 %.3f 秒，请检查 video.snapshot.after_span_s 配置",
                request.row_index,
                span_s,
            )

        results = [
            self._analyze_tube_safely(slot, slot_map.get(slot.tube_no), span_s=span_s)
            for slot in request.tubes
        ]
        return RowAnalysisResult(
            analyzer_info=self._info,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tubes=results,
        )

    def _analyze_tube_safely(
        self, slot: TubeSlot, paired: _PairedTube | None, *, span_s: float
    ) -> TubeAnalysis:
        try:
            return self._analyze_tube(slot, paired, span_s=span_s)
        except Exception as exc:
            _log.exception("管号 %d 后处理失败", slot.tube_no)
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.FAILED,
                message=f"单管判读异常：{exc}",
            )

    def _analyze_tube(
        self, slot: TubeSlot, paired: _PairedTube | None, *, span_s: float
    ) -> TubeAnalysis:
        cfg = self._config
        if paired is None:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                message="未检测到试管",
            )

        tube_t0, gel_t0 = paired.tube_t0, paired.gel_t0
        tube_t2, gel_t2 = paired.tube_t2, paired.gel_t2

        if tube_t0 is None or tube_t2 is None:
            if gel_t0 is None and gel_t2 is None:
                return TubeAnalysis(
                    tube_no=slot.tube_no,
                    status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                    message="未检测到试管",
                )
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.LOW_QUALITY,
                state=None,
                message="试管掩码缺失，无法计算判读得分",
            )

        base_extra: dict[str, float | int | str | bool | None] = {
            "frame_pair_mode": cfg.frame_pair_mode,
            "t0_t2_span_s": span_s,
            "x_center_t0": round(tube_t0.x_center, 1),
            "x_center_t2": round(tube_t2.x_center, 1),
            "tube_area_t0": tube_t0.area,
            "det_conf_tube_t0": round(tube_t0.conf, 4),
            "det_conf_tube_t2": round(tube_t2.conf, 4),
        }

        missing_before = gel_t0 is None
        missing_after = gel_t2 is None
        if missing_before or missing_after:
            confs = [tube_t0.conf, tube_t2.conf]
            if gel_t0 is not None:
                confs.append(gel_t0.conf)
                base_extra["gel_area_t0"] = gel_t0.area
                base_extra["det_conf_gel_t0"] = round(gel_t0.conf, 4)
            if gel_t2 is not None:
                confs.append(gel_t2.conf)
                base_extra["gel_area_t2"] = gel_t2.area
                base_extra["det_conf_gel_t2"] = round(gel_t2.conf, 4)
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.GEL_MISSING,
                state=None,
                confidence=float(np.clip(np.mean(confs), 0.0, 1.0)),
                features=GelFeatures(
                    area_before=None if gel_t0 is None else float(gel_t0.area),
                    area_after=None if gel_t2 is None else float(gel_t2.area),
                    centroid_before=None if gel_t0 is None else _centroid_xy(gel_t0.mask),
                    centroid_after=None if gel_t2 is None else _centroid_xy(gel_t2.mask),
                    gel_missing_before=missing_before,
                    gel_missing_after=missing_after,
                    extra={**base_extra, "raw_prediction": "REVIEW"},
                ),
                masks=TubeMasks(
                    tube_before=tube_t0.mask,
                    tube_after=tube_t2.mask,
                    gel_before=None if gel_t0 is None else gel_t0.mask,
                    gel_after=None if gel_t2 is None else gel_t2.mask,
                ),
                message="某一帧未检测到凝胶或无法匹配",
            )

        assert gel_t0 is not None and gel_t2 is not None
        breakdown = evaluate_gel_pair(
            gel_t0.mask, tube_t0.mask, gel_t2.mask, tube_t2.mask, cfg.scoring
        )
        if breakdown is None:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.LOW_QUALITY,
                state=None,
                message="试管掩码几何基准无效，无法计算判读得分",
            )

        area_before = float(gel_t0.area)
        area_after = float(gel_t2.area)
        delta_area_pct = (
            abs(area_after - area_before) / area_before * 100.0 if area_before > 0 else 0.0
        )
        if breakdown.prediction == "positive":
            state = GelState.FULLY_GELLED
            note: str | None = None
        elif breakdown.prediction == "negative":
            state = GelState.NOT_GELLED
            note = None
        else:
            state = GelState.PARTIALLY_GELLED
            note = breakdown.reason

        extra: dict[str, float | int | str | bool | None] = {
            **base_extra,
            "raw_prediction": breakdown.prediction,
            "gel_area_t0": gel_t0.area,
            "gel_area_t2": gel_t2.area,
            "det_conf_gel_t0": round(gel_t0.conf, 4),
            "det_conf_gel_t2": round(gel_t2.conf, 4),
            "fill_ratio_t0": breakdown.fill_ratio_t0,
            "height_norm_t2": breakdown.height_norm_t2,
            "curvature_t2": breakdown.curvature_t2,
            "fill_score": breakdown.fill_score,
            "height_score": breakdown.height_score,
            "flatness_score": breakdown.flatness_score,
        }
        conf_mean = float(
            np.clip(np.mean([tube_t0.conf, gel_t0.conf, tube_t2.conf, gel_t2.conf]), 0.0, 1.0)
        )

        return TubeAnalysis(
            tube_no=slot.tube_no,
            status=TubeAnalysisStatus.OK,
            state=state,
            score=min(1.0, max(0.0, breakdown.total_score)),
            confidence=conf_mean,
            features=GelFeatures(
                area_before=area_before,
                area_after=area_after,
                delta_area_pct=round(delta_area_pct, 4),
                centroid_before=_centroid_xy(gel_t0.mask),
                centroid_after=_centroid_xy(gel_t2.mask),
                extra=extra,
            ),
            masks=TubeMasks(
                tube_before=tube_t0.mask,
                tube_after=tube_t2.mask,
                gel_before=gel_t0.mask,
                gel_after=gel_t2.mask,
            ),
            message=note,
        )

    def close(self) -> None:
        """释放模型资源，可重复调用。"""
        self._segmenter.close()
        self._info = None
