"""ONNX Runtime 实例分割与凝胶特征判读核心。

从 ``yolov8seg_tubegel``（``cleanup/onnx-export-gel-seg`` 分支）的
``gel_inspection/predict_segment.py`` 与 ``judge_by_features.py`` 迁移而来，
改造为纯内存、会话常驻、带完整类型标注的推理模块，不依赖 ``ultralytics`` 与 PyTorch，
不写入临时磁盘文件。
"""

from __future__ import annotations

import ast
import ctypes
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

_log = logging.getLogger(__name__)

Uint8Array = NDArray[np.uint8]
Float32Array = NDArray[np.float32]
BoolArray = NDArray[np.bool_]


class SegmentConfig(BaseModel):
    """ONNX 实例分割超参数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conf: float = Field(default=0.6, gt=0.0, le=1.0)
    nms_iou: float = Field(default=0.7, gt=0.0, le=1.0)
    iou_dup_thres: float = Field(default=0.7, gt=0.0, le=1.0)
    min_area_ratio: float = Field(default=0.0005, ge=0.0, lt=1.0)
    device: int | str = "auto"
    intra_op_num_threads: int | None = Field(default=None, ge=1)


class FeatureScoringConfig(BaseModel):
    """基于 t0/t2 几何特征的加权评分配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    height_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    flatness_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    score_threshold: float = Field(default=0.6, gt=0.0, lt=1.0)
    score_review_buffer: float = Field(default=0.005, ge=0.0, lt=0.5)
    fill_full_score_at: float = Field(default=0.1, gt=0.0)
    height_full_score_range: tuple[float, float] = (0.45, 0.55)
    height_zero_score_at: tuple[float, float] = (0.25, 0.75)
    curvature_zero_score_at: float = Field(default=0.15, gt=0.0)
    max_x_dist: float = Field(default=120.0, gt=0.0)

    @model_validator(mode="after")
    def _validate_ranges(self) -> FeatureScoringConfig:
        low_full, high_full = self.height_full_score_range
        low_zero, high_zero = self.height_zero_score_at
        if not low_zero < low_full <= high_full < high_zero:
            raise ValueError(
                "height_zero_score_at 与 height_full_score_range 须满足 "
                "zero_low < full_low <= full_high < zero_high"
            )
        if self.fill_weight + self.height_weight + self.flatness_weight <= 0.0:
            raise ValueError("三项特征权重之和必须大于 0")
        return self


@dataclass(frozen=True, slots=True)
class LoadedModelMeta:
    """已加载 ONNX 模型的元数据。"""

    input_name: str
    input_height: int
    input_width: int
    output_names: tuple[str, ...]
    names: dict[int, str]
    gel_class_id: int
    tube_class_id: int
    provider: str


@dataclass(frozen=True, slots=True)
class SegmentedInstance:
    """单个分割实例（掩码与输入原图同尺寸，取值严格为 0/255）。"""

    mask: Uint8Array
    conf: float
    x_center: float
    area: int
    box_xyxy: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class FrameSegmentation:
    """单帧分割输出，按 x_center 升序排列。"""

    gels: tuple[SegmentedInstance, ...]
    tubes: tuple[SegmentedInstance, ...]


@dataclass(frozen=True, slots=True)
class FeatureScoreBreakdown:
    """一对 t0/t2 凝胶与试管的特征评分结果。"""

    fill_ratio_t0: float
    height_norm_t2: float
    curvature_t2: float
    fill_score: float
    height_score: float
    flatness_score: float
    total_score: float
    prediction: Literal["positive", "negative", "REVIEW"]
    reason: str


def inference_providers(device: int | str, available_providers: Sequence[str]) -> list[Any]:
    """按运行设备与可用提供程序构造 ONNX Runtime providers 列表。"""
    device_str = str(device).strip()
    if device_str.lower() in {"cpu", "-1"}:
        return ["CPUExecutionProvider"]
    if "CUDAExecutionProvider" not in available_providers:
        return ["CPUExecutionProvider"]
    if sys.platform == "linux":
        for library in ("libcudnn.so.9", "libcudart.so.12", "libcublas.so.12"):
            try:
                ctypes.CDLL(library)
            except OSError:
                _log.warning("%s 不可用，回退 CPUExecutionProvider", library)
                return ["CPUExecutionProvider"]
    if device_str.lower() in {"auto", "cuda", "gpu"}:
        device_id = 0
    else:
        if device_str.lower().startswith("cuda:"):
            device_str = device_str.partition(":")[2]
        device_id = int(device_str.split(",")[0])
    return [("CUDAExecutionProvider", {"device_id": device_id}), "CPUExecutionProvider"]


def mask_iou(m1: BoolArray, m2: BoolArray) -> float:
    """计算两张布尔掩码的 IoU。"""
    inter = int(np.logical_and(m1, m2).sum())
    union = int(np.logical_or(m1, m2).sum())
    return inter / union if union > 0 else 0.0


def letterbox(image: Uint8Array, shape: tuple[int, int]) -> tuple[Uint8Array, float, int, int]:
    """等比缩放到 ``shape=(H, W)`` 并以灰边 (114, 114, 114) 填充。"""
    height, width = image.shape[:2]
    target_height, target_width = shape
    ratio = min(target_height / height, target_width / width)
    resized_width, resized_height = round(width * ratio), round(height * ratio)
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_width = (target_width - resized_width) / 2
    pad_height = (target_height - resized_height) / 2
    left, right = round(pad_width - 0.1), round(pad_width + 0.1)
    top, bottom = round(pad_height - 0.1), round(pad_height + 0.1)
    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    return np.asarray(padded, dtype=np.uint8), ratio, left, top


def decode_detections(
    prediction: Float32Array,
    proto: Float32Array,
    class_count: int,
    conf: float,
    nms_iou: float,
) -> Float32Array:
    """解码 YOLOv8-seg 检测头输出并执行按类 NMS。"""
    mask_dim = int(proto.shape[0])
    feature_count = 4 + class_count + mask_dim
    pred = np.squeeze(prediction, axis=0)
    if pred.ndim != 2:
        raise ValueError(f"ONNX 分割输出维度无效：{pred.shape}")
    if pred.shape[0] == feature_count:
        pred = pred.T
    elif pred.shape[1] != feature_count:
        raise ValueError(
            f"ONNX 分割输出形状无效：{pred.shape}，"
            f"模型元数据声明 {class_count} 个类别和 {mask_dim} 个掩码系数"
        )

    class_scores = pred[:, 4 : 4 + class_count]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(class_scores)), class_ids]
    keep = scores > conf
    if not np.any(keep):
        return np.empty((0, 6 + mask_dim), dtype=np.float32)

    pred = pred[keep]
    scores = scores[keep]
    class_ids = class_ids[keep]
    center_x, center_y, box_width, box_height = pred[:, :4].T
    boxes = np.column_stack(
        (
            center_x - box_width / 2,
            center_y - box_height / 2,
            center_x + box_width / 2,
            center_y + box_height / 2,
        )
    )

    detections: list[int] = []
    for class_id in np.unique(class_ids):
        class_indices = np.flatnonzero(class_ids == class_id)
        class_boxes = boxes[class_indices]
        cv_boxes = [
            [float(x1), float(y1), float(x2 - x1), float(y2 - y1)] for x1, y1, x2, y2 in class_boxes
        ]
        kept = cv2.dnn.NMSBoxes(
            cv_boxes,
            scores[class_indices].astype(float).tolist(),
            conf,
            nms_iou,
        )
        if len(kept):
            detections.extend(class_indices[np.asarray(kept).reshape(-1)].tolist())

    if not detections:
        return np.empty((0, 6 + mask_dim), dtype=np.float32)

    det_arr = np.asarray(detections)
    order = np.argsort(scores[det_arr])[::-1][:300]
    det_arr = det_arr[order]
    return np.column_stack(
        (
            boxes[det_arr],
            scores[det_arr],
            class_ids[det_arr],
            pred[det_arr, 4 + class_count :],
        )
    ).astype(np.float32)


def restore_masks(
    proto: Float32Array,
    coefficients: Float32Array,
    boxes: Float32Array,
    original_shape: tuple[int, int],
) -> Uint8Array:
    """根据 proto 与掩码系数还原回原图尺寸的二值掩码（取值 0/1）。"""
    height, width = original_shape
    channels, mask_height, mask_width = proto.shape
    logits = coefficients @ proto.reshape(channels, -1)
    logits = logits.reshape(-1, mask_height, mask_width)

    gain = min(mask_height / height, mask_width / width)
    pad_width = (mask_width - round(width * gain)) / 2
    pad_height = (mask_height - round(height * gain)) / 2
    left, top = round(pad_width - 0.1), round(pad_height - 0.1)
    right, bottom = left + round(width * gain), top + round(height * gain)

    masks: list[Uint8Array] = []
    x_coords = np.arange(width)[None, :]
    y_coords = np.arange(height)[:, None]
    for mask_logits, (x1, y1, x2, y2) in zip(logits, boxes, strict=True):
        scaled = cv2.resize(
            mask_logits[top:bottom, left:right],
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        )
        mask = scaled > 0
        mask &= (x_coords >= x1) & (x_coords < x2) & (y_coords >= y1) & (y_coords < y2)
        masks.append(mask.astype(np.uint8))

    return np.stack(masks)


def get_y_bounds(mask: BoolArray) -> tuple[int, int, int]:
    """返回布尔掩码的 (y_min, y_max, height)。"""
    ys = np.where(mask)[0]
    if not len(ys):
        return 0, 0, 0
    y_min, y_max = int(ys.min()), int(ys.max())
    return y_min, y_max, y_max - y_min


def tube_width(mask: BoolArray) -> int:
    """返回试管掩码像素宽度。"""
    xs = np.where(mask)[1]
    return int(xs.max() - xs.min()) if len(xs) else 0


def get_bottom_curvature(mask: BoolArray, tube_w: int) -> float:
    """计算 t2 凝胶下缘 5%–95% 横向区间的归一化曲率。"""
    ys, xs = np.where(mask)
    if not len(xs):
        return 0.0
    x_min, x_max = int(xs.min()), int(xs.max())
    x_start = int(x_min + (x_max - x_min) * 0.05)
    x_end = int(x_min + (x_max - x_min) * 0.95)
    bottom_by_x = np.full(x_max - x_min + 1, -1, dtype=int)
    np.maximum.at(bottom_by_x, xs - x_min, ys)
    bottom_ys = bottom_by_x[x_start - x_min : x_end - x_min + 1]
    bottom_ys = bottom_ys[bottom_ys >= 0]
    curvature_px = int(bottom_ys.max() - bottom_ys.min()) if len(bottom_ys) else 0
    return float(curvature_px / tube_w) if tube_w > 0 else 0.0


def _clip_score(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def score_features(
    fill_ratio: float,
    height_norm: float,
    curvature: float,
    cfg: FeatureScoringConfig,
) -> tuple[float, float, float, float]:
    """将三项特征归一化到 [0, 1] 并计算加权总分。"""
    fill_score = _clip_score(fill_ratio / cfg.fill_full_score_at)
    full_low, full_high = cfg.height_full_score_range
    zero_low, zero_high = cfg.height_zero_score_at
    height_distance = max(full_low - height_norm, height_norm - full_high, 0.0)
    if height_norm < full_low:
        height_score = 1.0 - height_distance / (full_low - zero_low)
    elif height_norm > full_high:
        height_score = 1.0 - height_distance / (zero_high - full_high)
    else:
        height_score = 1.0
    height_score = _clip_score(height_score)
    flatness_score = _clip_score(1.0 - curvature / cfg.curvature_zero_score_at)
    total_score = (
        cfg.fill_weight * fill_score
        + cfg.height_weight * height_score
        + cfg.flatness_weight * flatness_score
    )
    return fill_score, height_score, flatness_score, total_score


def classify_score(
    score: float, cfg: FeatureScoringConfig
) -> Literal["positive", "negative", "REVIEW"]:
    """根据阈值与复核缓冲区返回阳性、阴性或待复核。"""
    lower = cfg.score_threshold - cfg.score_review_buffer
    upper = cfg.score_threshold + cfg.score_review_buffer
    if lower <= score <= upper:
        return "REVIEW"
    return "positive" if score > upper else "negative"


def evaluate_gel_pair(
    gel_t0: Uint8Array,
    tube_t0: Uint8Array,
    gel_t2: Uint8Array,
    tube_t2: Uint8Array,
    cfg: FeatureScoringConfig,
) -> FeatureScoreBreakdown | None:
    """依据配对的 t0/t2 掩码计算三项特征及加权判定结果。

    当试管掩码几何基准无效（面积或宽度为 0）时返回 None。
    """
    gel_t0_b = gel_t0 > 127
    tube_t0_b = tube_t0 > 127
    gel_t2_b = gel_t2 > 127
    tube_t2_b = tube_t2 > 127

    gel_area = int(gel_t0_b.sum())
    tube_area = int(tube_t0_b.sum())
    tube_w = tube_width(tube_t2_b)
    if tube_area <= 0 or tube_w <= 0:
        return None

    fill_t0 = gel_area / tube_area
    _, _, height = get_y_bounds(gel_t2_b)
    height_norm = height / tube_w
    curvature = get_bottom_curvature(gel_t2_b, tube_w)
    fill_s, height_s, flat_s, total_s = score_features(fill_t0, height_norm, curvature, cfg)
    total_rounded = round(total_s, 4)
    prediction = classify_score(total_rounded, cfg)
    if prediction == "REVIEW":
        low = cfg.score_threshold - cfg.score_review_buffer
        high = cfg.score_threshold + cfg.score_review_buffer
        reason = f"综合得分 {total_rounded:.2f} 落在复核区间 [{low:.2f}, {high:.2f}]，需人工复核"
    else:
        cmp_text = "高于" if prediction == "positive" else "低于"
        label_text = "阳性" if prediction == "positive" else "阴性"
        reason = f"综合得分 {total_rounded:.2f} {cmp_text}复核区间，判为{label_text}"

    return FeatureScoreBreakdown(
        fill_ratio_t0=round(fill_t0, 4),
        height_norm_t2=round(height_norm, 4),
        curvature_t2=round(curvature, 4),
        fill_score=round(fill_s, 4),
        height_score=round(height_s, 4),
        flatness_score=round(flat_s, 4),
        total_score=total_rounded,
        prediction=prediction,
        reason=reason,
    )


class OnnxGelSegmenter:
    """常驻会话的 ONNX Runtime 凝胶/试管实例分割器。"""

    def __init__(self, *, session_factory: Callable[..., Any] | None = None) -> None:
        """创建分割器实例，不导入 ``onnxruntime`` 也不加载权重。"""
        self._session_factory = session_factory
        self._session: Any | None = None
        self._meta: LoadedModelMeta | None = None

    @property
    def is_loaded(self) -> bool:
        """是否已加载模型并完成预热。"""
        return self._session is not None and self._meta is not None

    @property
    def meta(self) -> LoadedModelMeta | None:
        """当前已加载模型的元数据。"""
        return self._meta

    def load(self, model_path: Path, cfg: SegmentConfig) -> LoadedModelMeta:
        """加载 ONNX 模型并执行一次空白图预热。"""
        resolved = Path(model_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"ONNX 模型文件不存在：{resolved}")
        if resolved.suffix.lower() != ".onnx":
            raise ValueError(f"只支持 ONNX 模型（.onnx）：{resolved}")

        if self._session_factory is not None:
            session = self._session_factory(resolved, cfg)
        else:
            import onnxruntime as ort

            providers = inference_providers(cfg.device, ort.get_available_providers())
            sess_options = ort.SessionOptions()
            if cfg.intra_op_num_threads is not None:
                sess_options.intra_op_num_threads = cfg.intra_op_num_threads
            session = ort.InferenceSession(
                str(resolved), sess_options=sess_options, providers=providers
            )

        inputs = session.get_inputs()
        if not inputs:
            raise ValueError("ONNX 模型缺少输入节点")
        input_meta = inputs[0]
        input_shape = input_meta.shape
        if len(input_shape) != 4 or input_shape[1] != 3:
            raise ValueError(f"ONNX 模型输入形状不受支持：{input_shape}")
        input_height = int(input_shape[2]) if isinstance(input_shape[2], int) else 640
        input_width = int(input_shape[3]) if isinstance(input_shape[3], int) else 640

        outputs = session.get_outputs()
        output_names = tuple(str(output.name) for output in outputs)
        if len(output_names) < 2:
            raise ValueError(f"ONNX 分割模型必须至少有两个输出，当前为 {output_names}")

        metadata = session.get_modelmeta().custom_metadata_map
        try:
            raw_names = ast.literal_eval(metadata["names"])
        except (KeyError, SyntaxError, ValueError) as exc:
            raise ValueError("ONNX 模型缺少有效的类别名称元数据（names）") from exc
        if not isinstance(raw_names, dict) or not raw_names:
            raise ValueError("ONNX 模型类别名称元数据（names）必须是非空字典")
        names = {int(cid): str(name) for cid, name in raw_names.items()}

        gel_id = next((cid for cid, name in names.items() if name.strip().lower() == "gel"), None)
        tube_id = next((cid for cid, name in names.items() if name.strip().lower() == "tube"), None)
        if gel_id is None or tube_id is None:
            raise ValueError(f"ONNX 模型类别缺少 'gel' 或 'tube'：{names}")

        warmup = np.zeros((1, 3, input_height, input_width), dtype=np.float32)
        session.run(list(output_names[:2]), {str(input_meta.name): warmup})

        active_providers = session.get_providers()
        provider = str(active_providers[0]) if active_providers else "CPUExecutionProvider"
        meta = LoadedModelMeta(
            input_name=str(input_meta.name),
            input_height=input_height,
            input_width=input_width,
            output_names=output_names,
            names=names,
            gel_class_id=gel_id,
            tube_class_id=tube_id,
            provider=provider,
        )
        self._session = session
        self._meta = meta
        _log.info(
            "ONNX 分割模型已加载：%s（provider=%s, input=%dx%d, names=%s）",
            resolved.name,
            provider,
            input_width,
            input_height,
            names,
        )
        return meta

    def segment(self, image_bgr: Uint8Array, cfg: SegmentConfig) -> FrameSegmentation:
        """对单张只读 BGR 图像执行实例分割，返回按 x 坐标排序的实例列表。"""
        if self._session is None or self._meta is None:
            raise RuntimeError("ONNX 分割模型未加载")
        meta = self._meta
        orig_h, orig_w = image_bgr.shape[:2]
        padded, ratio, pad_left, pad_top = letterbox(
            image_bgr, (meta.input_height, meta.input_width)
        )
        tensor = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        tensor = np.ascontiguousarray(tensor[None], dtype=np.float32) / 255.0

        outputs = self._session.run(list(meta.output_names[:2]), {meta.input_name: tensor})
        proto = np.asarray(outputs[1][0], dtype=np.float32)
        detections = decode_detections(
            np.asarray(outputs[0], dtype=np.float32),
            proto,
            len(meta.names),
            cfg.conf,
            cfg.nms_iou,
        )
        if not len(detections):
            return FrameSegmentation(gels=(), tubes=())

        boxes = detections[:, :4].copy()
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_left) / ratio
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_top) / ratio
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, orig_w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, orig_h)
        raw_masks = restore_masks(proto, detections[:, 6:], boxes, (orig_h, orig_w))
        min_area = int(orig_h * orig_w * cfg.min_area_ratio)

        by_class: dict[
            int, list[tuple[NDArray[np.bool_], float, tuple[float, float, float, float]]]
        ] = {}
        for idx, raw_mask in enumerate(raw_masks):
            mask_bool = raw_mask > 0
            if int(mask_bool.sum()) < min_area:
                continue
            class_id = int(detections[idx, 5])
            box = (
                float(boxes[idx, 0]),
                float(boxes[idx, 1]),
                float(boxes[idx, 2]),
                float(boxes[idx, 3]),
            )
            by_class.setdefault(class_id, []).append((mask_bool, float(detections[idx, 4]), box))

        def build_instances(class_id: int) -> tuple[SegmentedInstance, ...]:
            items = by_class.get(class_id, [])
            items.sort(key=lambda item: item[1], reverse=True)
            kept: list[tuple[NDArray[np.bool_], float, tuple[float, float, float, float]]] = []
            for item in items:
                if any(mask_iou(item[0], existing[0]) > cfg.iou_dup_thres for existing in kept):
                    continue
                kept.append(item)
            instances: list[SegmentedInstance] = []
            for mask_bool, conf_val, box in kept:
                xs = np.where(mask_bool)[1]
                if not len(xs):
                    continue
                mask_u8 = np.where(mask_bool, np.uint8(255), np.uint8(0))
                instances.append(
                    SegmentedInstance(
                        mask=mask_u8,
                        conf=conf_val,
                        x_center=float(xs.mean()),
                        area=int(mask_bool.sum()),
                        box_xyxy=box,
                    )
                )
            instances.sort(key=lambda inst: inst.x_center)
            return tuple(instances)

        return FrameSegmentation(
            gels=build_instances(meta.gel_class_id),
            tubes=build_instances(meta.tube_class_id),
        )

    def close(self) -> None:
        """释放 ONNX Runtime 会话与元数据，可重复调用。"""
        self._session = None
        self._meta = None
