"""基于 ``inspection/`` 真实算法离线结果的视频源与分析器。

成对提供：
- :class:`InspectionVideoSource`：跟随 ``SimDevice`` 的排号与翻转阶段，切分 ``vis/*_judge.jpg``
  为翻转前（``t0``）与翻转后（``t2``）图像并等比填充到统一画幅；
- :class:`InspectionReplayAnalyzer`：复用 ``algorithm_json`` 解析 ``result.json``，按与视频源
  完全一致的排号映射与几何变换输出 ``RowAnalysisResult`` 和像素对齐的掩码。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import yaml
from numpy.typing import NDArray

from gelinspect.domain.plate import tube_to_position
from gelinspect.infrastructure.analysis.algorithm_json import (
    AlgorithmImageResult,
    AlgorithmInstance,
    AlgorithmRunResult,
    LetterboxTransform,
    convert_instance_to_tube_analysis,
    extract_thresholds,
    load_algorithm_run,
    read_binary_mask,
    resolve_run_asset,
)
from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysis,
    TubeAnalysisStatus,
)
from gelinspect.interfaces.device import PlatePlan
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)
from simulation.clock import Clock, SystemClock
from simulation.scene import SceneProvider, StaticScene
from simulation.sim_device import SimDevice

RowMappingMode = Literal["best_fit", "sequential"]
MissingPolicy = Literal["tube_not_found", "cycle"]
Uint8Array = NDArray[np.uint8]

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 1024
DEFAULT_FPS = 15.0
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_inspection_dir(raw: str | Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return _REPO_ROOT / path


def _select_run_dir(inspection_dir: Path, run_id: str | None) -> Path:
    if not inspection_dir.is_dir():
        raise FileNotFoundError(f"inspection 目录不存在：{inspection_dir}")
    if run_id and run_id.lower() != "latest":
        target = inspection_dir / run_id
        if not (target / "result.json").is_file():
            raise FileNotFoundError(f"指定的运行目录缺少 result.json：{target}")
        return target
    candidates = sorted(
        (p for p in inspection_dir.iterdir() if p.is_dir() and (p / "result.json").is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for candidate in candidates:
        try:
            loaded = load_algorithm_run(candidate)
        except Exception:
            continue
        if loaded.images:
            return candidate
    raise FileNotFoundError(f"{inspection_dir} 下没有包含有效 result.json 的运行目录")


def _detect_divider_x(vis: Uint8Array) -> int | None:
    """检测 ``t0`` 与 ``t2`` 拼接图中间的纯白竖直分隔线。"""
    h, w = vis.shape[:2]
    if w < 100:
        return None
    mid0 = w // 3
    mid1 = (w * 2) // 3
    col_min = vis[:, mid0:mid1].min(axis=(0, 2))
    white_cols = np.flatnonzero(col_min >= 245)
    if len(white_cols) == 0:
        return None
    return int(mid0 + white_cols[0])


def _read_bgr_image(path: Path) -> Uint8Array | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    decoded = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if decoded is None:
        return None
    return np.asarray(decoded, dtype=np.uint8)


def _panel_shape(run_dir: Path, stem: str, phase_tag: str) -> tuple[int, int] | None:
    masks_dir = run_dir / "masks"
    if not masks_dir.is_dir():
        return None
    for path in sorted(masks_dir.glob(f"{stem}_{phase_tag}_*.png")):
        mask = read_binary_mask(path)
        if mask is not None:
            return int(mask.shape[0]), int(mask.shape[1])
    return None


@dataclass(frozen=True, slots=True)
class PreparedRowMedia:
    """一对对齐后的翻转前后图像与几何变换。"""

    before_image: Uint8Array
    after_image: Uint8Array
    transform_before: LetterboxTransform
    transform_after: LetterboxTransform


class InspectionDataset:
    """加载一次 ``inspection/<run_id>`` 数据并提供确定性排号映射与图像切分。"""

    def __init__(
        self,
        inspection_dir: str | Path = "inspection",
        run_id: str | None = None,
        *,
        row_mapping: RowMappingMode = "best_fit",
    ) -> None:
        """加载指定 ``inspection/<run_id>`` 目录。"""
        self.inspection_dir = _resolve_inspection_dir(inspection_dir)
        self.run_dir = _select_run_dir(self.inspection_dir, run_id)
        self.run_id = self.run_dir.name
        self.run: AlgorithmRunResult = load_algorithm_run(self.run_dir)
        if not self.run.images:
            raise ValueError(f"{self.run_dir / 'result.json'} 中没有任何图像结果")
        self.row_mapping = row_mapping
        self._panel_cache: OrderedDict[tuple[str, int, int], PreparedRowMedia] = OrderedDict()

    def select_image(self, row_index: int, tube_count: int) -> AlgorithmImageResult:
        """确定性选择本排对应的算法图片结果。"""
        images = self.run.images
        if self.row_mapping == "sequential" or tube_count <= 0:
            return images[(row_index - 1) % len(images)]
        enough = [img for img in images if len(img.instances) >= tube_count]
        if enough:
            min_len = min(len(img.instances) for img in enough)
            candidates = [img for img in enough if len(img.instances) == min_len]
        else:
            max_len = max(len(img.instances) for img in images)
            candidates = [img for img in images if len(img.instances) == max_len]
        return candidates[(row_index - 1) % len(candidates)]

    def prepare_media(
        self, image_result: AlgorithmImageResult, target_shape: tuple[int, int]
    ) -> PreparedRowMedia:
        """拆出 ``t0`` / ``t2`` 图像并等比填充到 ``target_shape=(H, W)``。"""
        dst_h, dst_w = target_shape
        key = (image_result.stem, dst_h, dst_w)
        cached = self._panel_cache.get(key)
        if cached is not None:
            self._panel_cache.move_to_end(key)
            return cached

        vis_path = resolve_run_asset(self.run_dir, image_result.vis_path, "vis")
        if vis_path is None:
            fallback = self.run_dir / "vis" / f"{image_result.stem}_judge.jpg"
            vis_path = fallback if fallback.is_file() else None
        vis = _read_bgr_image(vis_path) if vis_path is not None else None

        is_dual = image_result.t0_image_path is not None or any(
            inst.mask_path and "_t0_" in inst.mask_path for inst in image_result.instances
        )
        if vis is None:
            blank = np.zeros((dst_h, dst_w, 3), dtype=np.uint8)
            tr = LetterboxTransform.fit((dst_h, dst_w), target_shape)
            prepared = PreparedRowMedia(blank, blank.copy(), tr, tr)
            self._panel_cache[key] = prepared
            return prepared

        vh, vw = vis.shape[:2]
        if is_dual:
            t0_shape = _panel_shape(self.run_dir, image_result.stem, "t0")
            t2_shape = _panel_shape(self.run_dir, image_result.stem, "t2")
            if t0_shape is not None:
                h0 = min(vh, t0_shape[0])
                w0 = min(vw, t0_shape[1])
                raw_before = vis[:h0, :w0]
                divider = _detect_divider_x(vis)
                start_x = (
                    divider + 5 if divider is not None and divider >= w0 - 2 else min(vw, w0 + 5)
                )
                if t2_shape is not None:
                    h2 = min(vh, t2_shape[0])
                    end_x = min(vw, start_x + t2_shape[1])
                else:
                    h2 = vh
                    end_x = vw
                raw_after = vis[:h2, start_x:end_x] if start_x < end_x else vis[:, w0:]
            else:
                div = _detect_divider_x(vis)
                split = div if div is not None else vw // 2
                raw_before = vis[:, :split]
                raw_after = vis[:, min(vw, split + 5) :]
        else:
            raw_before = vis
            raw_after = vis

        if raw_before.size == 0:
            raw_before = vis
        if raw_after.size == 0:
            raw_after = raw_before

        tr_before = LetterboxTransform.fit(
            (int(raw_before.shape[0]), int(raw_before.shape[1])), target_shape
        )
        tr_after = LetterboxTransform.fit(
            (int(raw_after.shape[0]), int(raw_after.shape[1])), target_shape
        )
        prepared = PreparedRowMedia(
            before_image=tr_before.apply_image(raw_before),
            after_image=tr_after.apply_image(raw_after),
            transform_before=tr_before,
            transform_after=tr_after,
        )
        self._panel_cache[key] = prepared
        if len(self._panel_cache) > 16:
            self._panel_cache.popitem(last=False)
        return prepared


class InspectionVideoSource:
    """``VideoSource`` 的 ``inspection/`` 回放实现，可无参构造。"""

    def __init__(
        self,
        *,
        inspection_dir: str | Path = "inspection",
        run_id: str | None = None,
        row_mapping: RowMappingMode = "best_fit",
        scene: SceneProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建回放视频源，不在构造时读取磁盘。"""
        self._inspection_dir = inspection_dir
        self._run_id = run_id
        self._row_mapping: RowMappingMode = row_mapping
        self.scene = scene
        self._clock: Clock = clock or SystemClock()
        self._static = StaticScene()
        self._dataset: InspectionDataset | None = None
        self._opened = False
        self._disconnected = False
        self._config = VideoConfig(device="inspection")
        self._width = DEFAULT_WIDTH
        self._height = DEFAULT_HEIGHT
        self._interval = 1.0 / DEFAULT_FPS
        self._next_due = 0.0
        self._sequence = 0

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开数据集并准备视频流。"""
        try:
            self._dataset = InspectionDataset(
                self._inspection_dir, self._run_id, row_mapping=self._row_mapping
            )
        except Exception as exc:
            raise VideoOpenError(f"无法打开 inspection 回放数据：{exc}") from exc
        self._width = config.width or DEFAULT_WIDTH
        self._height = config.height or DEFAULT_HEIGHT
        fps = config.fps or DEFAULT_FPS
        self._config = config
        self._interval = 1.0 / fps
        self._next_due = self._clock.monotonic()
        self._sequence = 0
        self._disconnected = False
        self._opened = True
        return VideoInfo(
            width=self._width,
            height=self._height,
            fps=fps,
            is_live=True,
            description=f"inspection replay ({self._dataset.run_id})",
        )

    def read(self) -> VideoFrame | None:
        """按帧率输出当前排翻转前或翻转后的真实样本图像。"""
        if not self._opened or self._dataset is None:
            raise VideoNotOpenError("inspection 视频源未打开")
        if self._disconnected:
            raise VideoDisconnectedError("inspection 视频源已断开")
        timeout = self._config.read_timeout_ms / 1000.0
        wait = self._next_due - self._clock.monotonic()
        if wait > timeout:
            self._clock.sleep(timeout)
            return None
        self._clock.sleep(wait)
        now = self._clock.monotonic()
        self._next_due = max(self._next_due + self._interval, now - 1.0)

        provider = self._provider()
        snapshot = provider.scene_snapshot()
        tube_count = self._tube_count_for_row(provider, snapshot.row_index)
        img_res = self._dataset.select_image(snapshot.row_index, tube_count)
        media = self._dataset.prepare_media(img_res, (self._height, self._width))
        frame_img = (
            media.before_image.copy() if snapshot.flip_phase < 0.5 else media.after_image.copy()
        )
        # 极轻量角点微调：防止静态图像触发视频管线的冻结帧检测，不影响试管区域。
        frame_img[-2:, -2:, :] = np.uint8((self._sequence % 2) * 3)
        captured_at = snapshot.at or self._clock.now()
        frame = VideoFrame(
            image=frame_img,
            sequence=self._sequence,
            captured_at=captured_at,
            monotonic_s=now,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        """关闭视频源。"""
        self._opened = False

    def _provider(self) -> SceneProvider:
        if self.scene is not None:
            return self.scene
        device = SimDevice.latest()
        return device if device is not None else self._static

    @staticmethod
    def _tube_count_for_row(provider: SceneProvider, row_index: int) -> int:
        plan: PlatePlan | None = provider.plate_plan if isinstance(provider, SimDevice) else None
        if plan is None:
            return 8
        return sum(1 for t in plan.tubes if tube_to_position(t.tube_no).row_index == row_index)


class InspectionReplayAnalyzer:
    """``GelAnalyzer`` 的 ``inspection/`` 回放实现，可无参构造。"""

    def __init__(
        self,
        *,
        inspection_dir: str | Path = "inspection",
        run_id: str | None = None,
        row_mapping: RowMappingMode = "best_fit",
        missing_policy: MissingPolicy = "tube_not_found",
        review_margin: float = 0.01,
    ) -> None:
        """创建回放分析器，不在构造时加载数据。"""
        self._inspection_dir: str | Path = inspection_dir
        self._run_id = run_id
        self._row_mapping: RowMappingMode = row_mapping
        self._missing_policy: MissingPolicy = missing_policy
        self._review_margin = review_margin
        self._dataset: InspectionDataset | None = None
        self._info: AnalyzerInfo | None = None

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """读取可选 YAML 配置并加载 ``inspection/result.json``。"""
        cfg: dict[str, Any] = {}
        config_version = "inspection-default"
        if config_path is not None:
            try:
                loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ValueError("配置文件顶层必须是字典")
                cfg = loaded
                config_version = f"inspection-{config_path.stem}"
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                raise AnalyzerError(f"无法读取 inspection 算法配置 {config_path}：{exc}") from exc

        inspection_dir = cfg.get("inspection_dir", self._inspection_dir)
        run_id = cfg.get("run_id", self._run_id)
        row_mapping: RowMappingMode = cfg.get("row_mapping", self._row_mapping)
        missing_policy: MissingPolicy = cfg.get("missing_policy", self._missing_policy)
        review_margin = float(cfg.get("review_margin", self._review_margin))
        try:
            dataset = InspectionDataset(inspection_dir, run_id, row_mapping=row_mapping)
        except Exception as exc:
            raise AnalyzerError(f"加载 inspection 数据失败：{exc}") from exc

        self._inspection_dir = inspection_dir
        self._run_id = run_id
        self._row_mapping = row_mapping
        self._missing_policy = missing_policy
        self._review_margin = review_margin
        self._dataset = dataset
        self._info = AnalyzerInfo(
            model_version=f"inspection-{dataset.run_id}",
            config_version=config_version,
            thresholds=extract_thresholds(dataset.run.summary, review_margin=review_margin),
        )
        return self._info

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """按排号映射读取对应样本结果并输出像素对齐的 ``RowAnalysisResult``。"""
        if self._dataset is None or self._info is None:
            raise AnalyzerError("inspection 分析器未加载")
        started = time.perf_counter()
        image_result = self._dataset.select_image(request.row_index, len(request.tubes))
        media = self._dataset.prepare_media(image_result, request.image_size)
        instances = sorted(image_result.instances, key=lambda inst: inst.position)

        tubes_out: list[TubeAnalysis] = []
        slots_sorted = sorted(request.tubes, key=lambda s: s.position_in_row)
        for idx, slot in enumerate(slots_sorted):
            inst: AlgorithmInstance | None = None
            if idx < len(instances):
                inst = instances[idx]
            elif self._missing_policy == "cycle" and instances:
                inst = instances[idx % len(instances)]

            if inst is None:
                tubes_out.append(
                    TubeAnalysis(
                        tube_no=slot.tube_no,
                        status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                        message=(
                            f"样本 {image_result.stem} 仅有 {len(instances)} 支试管，"
                            f"未匹配到第 {idx + 1} 支"
                        ),
                    )
                )
                continue

            tubes_out.append(
                convert_instance_to_tube_analysis(
                    slot.tube_no,
                    inst,
                    run_dir=self._dataset.run_dir,
                    image_result=image_result,
                    target_shape=request.image_size,
                    transform_before=media.transform_before,
                    transform_after=media.transform_after,
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return RowAnalysisResult(
            analyzer_info=self._info,
            elapsed_ms=elapsed_ms,
            tubes=tubes_out,
        )

    def close(self) -> None:
        """释放数据集引用，可重复调用。"""
        self._dataset = None
        self._info = None
