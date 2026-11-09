"""模拟算法：``GelAnalyzer`` 的参考实现，严格遵守 ``interfaces/analysis.py``。

默认的**测量模式**真的处理输入图像：

1. 按合成器的已知布局定位试管（管位、管口方向），检查图像亮度、噪声和管壁是否存在；
2. 在管内用阈值分割样品团（相对背光的透光率低于 ``GEL_VISIBLE_TRANSMITTANCE``），
   取最大连通域，面积小于 50 像素记为凝胶缺失；
3. 在试管坐标系（管口为原点，轴向指向管底，按管长归一化）中计算 ΔA、Δs、σ_A、σ_s。
   ΔA、Δs 取绝对值；σ_A 是帧间面积的变异系数（%），σ_s 是帧间轴向位置的标准差；
4. ``Score = 0.5·f(ΔA) + 0.3·f(Δs) + 0.1·f(σ_A) + 0.1·f(σ_s)``，每项 ``f`` 把指标线性映射到
   0–1，越大越凝固；Score ≥ 0.85 为完全凝固，≤ 0.25 为未凝固，其余为部分凝固。
   权重、阈值和归一化上限都可以在 YAML 配置里改（``weights``、``thresholds``、``limits``），
   对应算法方案“阈值标定”和“支持 YAML 配置动态调整”。每支管返回评分分项 ``score_terms``，
   ``AnalyzerInfo`` 带出当前阈值，供界面逐项展示判读过程。

**脚本化模式**（``scripted``）按 :class:`~simulation.scenario.Scenario` 给出状态，并生成与状态相符的
特征，掩码仍按图像计算，用于没有合成图可用时的演示。

故障注入：按管号指定 ``low_quality``、``failed``、``tube_not_found``，整体故障
（``fail_load``、``fail_all``），以及模拟耗时。全部可以通过 :meth:`MockAnalyzer.load` 的 YAML
配置设置，示例见 ``simulation/analyzer.example.yaml``。
"""

from __future__ import annotations

import math
import time
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from numpy.typing import NDArray

from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    CapturedFrame,
    GelFeatures,
    GelState,
    RowAnalysisRequest,
    RowAnalysisResult,
    ScoreTerm,
    StateThresholds,
    TubeAnalysis,
    TubeAnalysisStatus,
    TubeMasks,
    TubeSlot,
)
from simulation.image_synth import (
    GEL_VISIBLE_TRANSMITTANCE,
    SynthConfig,
    TubeGeometry,
    capsule_sdf,
    largest_component,
    slot_geometry,
    tube_box,
    tube_frame,
)
from simulation.scenario import Scenario

MIN_GEL_AREA = 50
"""算法方案第 5 节：面积低于 50 像素记为凝胶缺失。"""
FULLY_GELLED_SCORE = 0.85
"""默认的完全凝固阈值。"""
NOT_GELLED_SCORE = 0.25
"""默认的未凝固阈值。"""
DEFAULT_WEIGHTS: tuple[float, float, float, float] = (0.5, 0.3, 0.1, 0.1)
"""算法方案第 5 节的初始权重 ω1–ω4，对应 ΔA、Δs、σ_A、σ_s。"""
DEFAULT_LIMITS: tuple[float, float, float, float] = (30.0, 0.15, 10.0, 0.05)
"""归一化上限：指标达到上限时 f = 0。ΔA 和 σ_A 单位 %，Δs 和 σ_s 按管长归一化。"""
SCORE_KEYS = ("delta_area_pct", "delta_s", "sigma_area", "sigma_axial")
_DARK_LEVEL = 90.0
_BRIGHT_LEVEL = 250.0
_MAX_NOISE_RATIO = 0.10
_MAX_WALL_RATIO = 0.90
_MOUTH_EXCLUDE = 0.035

Uint8Array = NDArray[np.uint8]


class _Measurement:
    """一帧中一支试管的测量结果。"""

    def __init__(
        self,
        gel: Uint8Array | None,
        area: float,
        centroid: tuple[float, float] | None,
        axial: float | None,
    ) -> None:
        self.gel = gel
        self.area = area
        self.centroid = centroid
        self.axial = axial

    @property
    def missing(self) -> bool:
        """凝胶是否缺失。"""
        return self.gel is None


class _QualityError(Exception):
    """图像质量不足或找不到试管，携带对应的状态。"""

    def __init__(self, status: TubeAnalysisStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _linear(value: float, limit: float) -> float:
    """把 0–limit 线性映射到 1–0。"""
    return min(max(1.0 - value / limit, 0.0), 1.0)


def score_terms(
    delta_area_pct: float,
    delta_s: float,
    sigma_area_pct: float,
    sigma_axial: float,
    *,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    limits: tuple[float, float, float, float] = DEFAULT_LIMITS,
) -> dict[str, tuple[float, float]]:
    """融合评分的四个分项。

    Args:
        delta_area_pct: ΔA，单位 %。
        delta_s: Δs，按管长归一化。
        sigma_area_pct: σ_A，单位 %。
        sigma_axial: σ_s，按管长归一化。
        weights: 权重 ω1–ω4。
        limits: 归一化上限。

    Returns:
        特征名到 ``(权重, 归一化得分)``。
    """
    values = (delta_area_pct, delta_s, sigma_area_pct, sigma_axial)
    return {
        key: (weight, _linear(value, limit))
        for key, weight, value, limit in zip(SCORE_KEYS, weights, values, limits, strict=True)
    }


def compute_score(
    delta_area_pct: float,
    delta_s: float,
    sigma_area_pct: float,
    sigma_axial: float,
    *,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    limits: tuple[float, float, float, float] = DEFAULT_LIMITS,
) -> float:
    """融合评分，越大越凝固。

    Args:
        delta_area_pct: ΔA，单位 %。
        delta_s: Δs，按管长归一化。
        sigma_area_pct: σ_A，单位 %。
        sigma_axial: σ_s，按管长归一化。
        weights: 权重 ω1–ω4。
        limits: 归一化上限。

    Returns:
        0–1 的评分。
    """
    terms = score_terms(
        delta_area_pct, delta_s, sigma_area_pct, sigma_axial, weights=weights, limits=limits
    )
    return float(sum(w * f for w, f in terms.values()))


def state_from_score(
    score: float,
    fully_gelled_min: float = FULLY_GELLED_SCORE,
    not_gelled_max: float = NOT_GELLED_SCORE,
) -> GelState:
    """按评分判定三态。

    Args:
        score: 融合评分。
        fully_gelled_min: 完全凝固阈值。
        not_gelled_max: 未凝固阈值。

    Returns:
        凝固三态。
    """
    if score >= fully_gelled_min:
        return GelState.FULLY_GELLED
    if score <= not_gelled_max:
        return GelState.NOT_GELLED
    return GelState.PARTIALLY_GELLED


def _confidence(
    score: float,
    fully_gelled_min: float = FULLY_GELLED_SCORE,
    not_gelled_max: float = NOT_GELLED_SCORE,
) -> float:
    """离判定阈值越远越有把握。"""
    margin = min(abs(score - fully_gelled_min), abs(score - not_gelled_max))
    return float(min(0.99, 0.6 + 2.0 * margin))


def _four(value: Any, name: str) -> tuple[float, float, float, float]:
    """读取 4 个数的配置项。"""
    numbers = tuple(float(x) for x in value)
    if len(numbers) != 4:
        raise ValueError(f"{name} 需要 4 个数")
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def _gray(region: NDArray[np.uint8]) -> NDArray[np.float32]:
    return cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _measure(
    image: Uint8Array, geometry: TubeGeometry, config: SynthConfig, *, inverted: bool
) -> _Measurement:
    """测量一帧中一支试管。

    Args:
        image: BGR 整排图像。
        geometry: 试管几何。
        config: 与图像尺寸一致的布局参数。
        inverted: 该帧中试管是否已翻转（管口朝下）。

    Returns:
        测量结果，``gel`` 为整图尺寸的掩码，缺失时为 None。

    Raises:
        _QualityError: 图像过暗、过曝、噪声过大，或没有找到管壁。
    """
    x0, x1, y0, y1 = tube_box(geometry, config, margin=2.2)
    region = _gray(image[y0:y1, x0:x1])
    scale = -1.0 if inverted else 1.0
    u, s = tube_frame(geometry, (x0, x1, y0, y1), scale)
    length = geometry.length
    d_out = capsule_sdf(u, s, geometry.radius, length)
    d_in = capsule_sdf(u, s, geometry.inner_radius, length)

    side = (d_out > 6) & (d_out < 0.6 * geometry.radius + 6) & (np.abs(u) > 0)
    if not side.any():
        raise _QualityError(TubeAnalysisStatus.TUBE_NOT_FOUND, "管位超出图像范围")
    side_vals = np.where(side, region, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        background_rows = np.nanmedian(side_vals, axis=1)
    valid_rows = ~np.isnan(background_rows)
    level = float(np.median(background_rows[valid_rows])) if valid_rows.any() else 0.0
    if level < _DARK_LEVEL:
        raise _QualityError(TubeAnalysisStatus.LOW_QUALITY, "图像过暗")
    if level > _BRIGHT_LEVEL:
        raise _QualityError(TubeAnalysisStatus.LOW_QUALITY, "图像过曝")
    rows = np.where(valid_rows, background_rows, level).astype(np.float32)
    deviation = float(np.nanstd((side_vals - rows[:, None])[side]) / level)
    if deviation > _MAX_NOISE_RATIO:
        raise _QualityError(TubeAnalysisStatus.LOW_QUALITY, "背景噪声过大")

    transmit = region / rows[:, None]
    axial = s / np.float32(length)
    wall = (d_out < 0.5) & (d_in > 0.5) & (axial > 0.1) & (axial < 0.8)
    ring = (d_out > 3) & (d_out < 8) & (axial > 0.1) & (axial < 0.8)
    if not wall.any() or not ring.any():
        raise _QualityError(TubeAnalysisStatus.TUBE_NOT_FOUND, "未检测到试管")
    ratio = float(transmit[wall].mean() / max(float(transmit[ring].mean()), 1e-6))
    if ratio > _MAX_WALL_RATIO:
        raise _QualityError(TubeAnalysisStatus.TUBE_NOT_FOUND, "未检测到试管壁")

    core = (d_in < -1.0) & (axial > _MOUTH_EXCLUDE)
    candidate = np.zeros(transmit.shape, dtype=np.uint8)
    candidate[(transmit < GEL_VISIBLE_TRANSMITTANCE) & core] = 255
    opened = np.asarray(
        cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), dtype=np.uint8
    )
    gel_local = largest_component(opened)
    area = float(np.count_nonzero(gel_local))
    if area < MIN_GEL_AREA:
        return _Measurement(None, area, None, None)
    ys, xs = np.nonzero(gel_local)
    gel = np.zeros(image.shape[:2], dtype=np.uint8)
    gel[y0:y1, x0:x1] = gel_local
    centroid = (float(xs.mean() + x0), float(ys.mean() + y0))
    return _Measurement(gel, area, centroid, float(axial[gel_local > 0].mean()))


def _tube_mask(geometry: TubeGeometry, config: SynthConfig, *, inverted: bool) -> Uint8Array:
    """试管掩码，整图尺寸。"""
    box = tube_box(geometry, config)
    u, s = tube_frame(geometry, box, -1.0 if inverted else 1.0)
    inside = capsule_sdf(u, s, geometry.radius, geometry.length) < 0
    mask = np.zeros((config.height, config.width), dtype=np.uint8)
    x0, x1, y0, y1 = box
    mask[y0:y1, x0:x1] = inside.astype(np.uint8) * 255
    return mask


def _is_inverted(flip_angle_deg: float | None) -> bool:
    """翻转后试管是否倒置：没有角度时按 180° 处理。"""
    if flip_angle_deg is None:
        return True
    return math.cos(math.radians(flip_angle_deg)) < 0


def _std_series(values: list[float]) -> float | None:
    return float(np.std(values)) if len(values) >= 2 else None


class MockAnalyzer:
    """模拟的 ``GelAnalyzer``。

    构造参数都有默认值，可以无参构造；构造时不做任何耗时操作。

    Attributes:
        scenario: 脚本化模式使用的场景。
        scripted: 为真时按场景给出状态，否则测量图像。
        tube_faults: 按管号注入的单支故障状态。
        delay_ms: 模拟的每排耗时，计入 ``elapsed_ms`` 并通过 ``sleep`` 等待。
        fail_load: 为真时 ``load`` 抛 ``AnalyzerError``。
        fail_all: 为真时 ``analyze_row`` 抛 ``AnalyzerError``。
    """

    def __init__(
        self,
        *,
        scenario: Scenario | None = None,
        scripted: bool = False,
        tube_faults: Mapping[int, TubeAnalysisStatus] | None = None,
        delay_ms: float = 0.0,
        fail_load: bool = False,
        fail_all: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """创建模拟算法。

        Args:
            scenario: 场景，默认 ``Scenario()``。
            scripted: 是否使用脚本化模式。
            tube_faults: 按管号注入的故障，值只能是 ``LOW_QUALITY``、``FAILED``、
                ``TUBE_NOT_FOUND``。
            delay_ms: 模拟耗时，毫秒。
            fail_load: 是否让 ``load`` 失败。
            fail_all: 是否让 ``analyze_row`` 整体失败。
            sleep: 等待函数，测试中可以换成空操作。

        Raises:
            ValueError: 故障状态不被支持，或耗时为负。
        """
        self.scenario = scenario or Scenario()
        self.scripted = scripted
        self.tube_faults = dict(tube_faults or {})
        self.delay_ms = delay_ms
        self.fail_load = fail_load
        self.fail_all = fail_all
        self.weights = DEFAULT_WEIGHTS
        self.limits = DEFAULT_LIMITS
        self.thresholds = StateThresholds(
            not_gelled_max=NOT_GELLED_SCORE, fully_gelled_min=FULLY_GELLED_SCORE
        )
        self._sleep = sleep
        self._info: AnalyzerInfo | None = None
        self._validate()

    def _validate(self) -> None:
        allowed = {
            TubeAnalysisStatus.LOW_QUALITY,
            TubeAnalysisStatus.FAILED,
            TubeAnalysisStatus.TUBE_NOT_FOUND,
        }
        bad = [no for no, status in self.tube_faults.items() if status not in allowed]
        if bad:
            raise ValueError(f"管号 {bad} 的故障状态只能是 low_quality、failed 或 tube_not_found")
        if self.delay_ms < 0:
            raise ValueError("delay_ms 不能为负")
        if len(self.weights) != 4 or any(w < 0 for w in self.weights):
            raise ValueError("weights 需要 4 个非负数")
        if abs(sum(self.weights) - 1.0) > 1e-6:
            raise ValueError("weights 之和必须为 1")
        if len(self.limits) != 4 or any(v <= 0 for v in self.limits):
            raise ValueError("limits 需要 4 个正数")

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """读取可选的 YAML 配置并“加载模型”。

        配置键：``scripted``、``seed``、``state_ratios``、``tube_states``、``tube_faults``、
        ``delay_ms``、``fail_load``、``fail_all``。

        Args:
            config_path: 配置文件，None 时使用构造参数。

        Returns:
            版本信息，配置版本带有配置文件名。

        Raises:
            AnalyzerError: 配置无法读取，或 ``fail_load`` 为真。
        """
        config_version = "mock-default"
        if config_path is not None:
            try:
                self._apply_config(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                raise AnalyzerError(f"无法读取算法配置 {config_path}：{exc}") from exc
            config_version = f"mock-{config_path.stem}"
        if self.fail_load:
            raise AnalyzerError("模拟的模型加载失败")
        self._info = AnalyzerInfo(
            model_version="mock-1.0", config_version=config_version, thresholds=self.thresholds
        )
        return self._info

    def _apply_config(self, data: Mapping[str, Any]) -> None:
        """应用 YAML 配置。"""
        seed = int(data.get("seed", self.scenario.seed))
        ratios = tuple(float(x) for x in data.get("state_ratios", self.scenario.state_ratios))
        if len(ratios) != 3:
            raise ValueError("state_ratios 需要 3 个数")
        states: dict[int, GelState | None] = dict(self.scenario.tube_states)
        for key, value in (data.get("tube_states") or {}).items():
            states[int(key)] = None if value is None else GelState(value)
        self.scenario = Scenario(
            seed=seed, state_ratios=(ratios[0], ratios[1], ratios[2]), tube_states=states
        )
        self.scripted = bool(data.get("scripted", self.scripted))
        for key, value in (data.get("tube_faults") or {}).items():
            self.tube_faults[int(key)] = TubeAnalysisStatus(value)
        self.delay_ms = float(data.get("delay_ms", self.delay_ms))
        self.fail_load = bool(data.get("fail_load", self.fail_load))
        self.fail_all = bool(data.get("fail_all", self.fail_all))
        if "weights" in data:
            self.weights = _four(data["weights"], "weights")
        if "limits" in data:
            self.limits = _four(data["limits"], "limits")
        if "thresholds" in data:
            raw = data["thresholds"] or {}
            self.thresholds = StateThresholds(
                not_gelled_max=float(raw.get("not_gelled_max", NOT_GELLED_SCORE)),
                fully_gelled_min=float(raw.get("fully_gelled_min", FULLY_GELLED_SCORE)),
            )
        self._validate()

    def close(self) -> None:
        """释放资源，可重复调用。"""
        self._info = None

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """分析一排。

        Args:
            request: 分析请求。

        Returns:
            每支试管一条结果。

        Raises:
            AnalyzerError: 尚未加载，或注入了整体故障。
        """
        if self._info is None:
            raise AnalyzerError("模型未加载")
        if self.fail_all:
            raise AnalyzerError("模拟的算法整体故障")
        started = time.perf_counter()
        if self.delay_ms > 0:
            self._sleep(self.delay_ms / 1000)
        tubes = [self._analyze_safely(slot, request) for slot in request.tubes]
        compute_ms = (time.perf_counter() - started) * 1000
        return RowAnalysisResult(
            analyzer_info=self._info,
            elapsed_ms=compute_ms + self.delay_ms,
            tubes=tubes,
        )

    def _analyze_safely(self, slot: TubeSlot, request: RowAnalysisRequest) -> TubeAnalysis:
        try:
            fault = self.tube_faults.get(slot.tube_no)
            if fault is not None:
                return TubeAnalysis(
                    tube_no=slot.tube_no, status=fault, message=f"注入的故障：{fault.value}"
                )
            return self._analyze_tube(slot, request)
        except _QualityError as exc:
            return TubeAnalysis(tube_no=slot.tube_no, status=exc.status, message=exc.message)
        except Exception as exc:  # 单支问题不向外抛，转换为 status
            return TubeAnalysis(
                tube_no=slot.tube_no, status=TubeAnalysisStatus.FAILED, message=str(exc)
            )

    def _analyze_tube(self, slot: TubeSlot, request: RowAnalysisRequest) -> TubeAnalysis:
        height, width = request.image_size
        config = SynthConfig(width=width, height=height)
        geometry = slot_geometry(config, slot.position_in_row)
        inverted = _is_inverted(request.flip_angle_deg)
        before_series = self._series(request.frames_before, geometry, config, inverted=False)
        after_series = self._series(request.frames_after, geometry, config, inverted=inverted)
        before, after = before_series[-1], after_series[-1]
        tube_before = _tube_mask(geometry, config, inverted=False)
        tube_after = _tube_mask(geometry, config, inverted=inverted)

        if self.scripted:
            state = self.scenario.state_for(slot.tube_no)
            if state is None:
                return TubeAnalysis(
                    tube_no=slot.tube_no,
                    status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                    message="场景中该管位没有试管",
                )
            return self._scripted(slot, state, geometry, before, after, tube_before, tube_after)

        sigma_area, sigma_axial = self._sigmas(after_series, before_series)
        features = GelFeatures(
            area_before=None if before.missing else before.area,
            area_after=None if after.missing else after.area,
            centroid_before=before.centroid,
            centroid_after=after.centroid,
            axial_before=before.axial,
            axial_after=after.axial,
            sigma_area=sigma_area,
            sigma_axial=sigma_axial,
            gel_missing_before=before.missing,
            gel_missing_after=after.missing,
        )
        masks = TubeMasks(
            tube_before=tube_before,
            tube_after=tube_after,
            gel_before=before.gel,
            gel_after=after.gel,
        )
        if before.missing or after.missing:
            state = None if before.missing else GelState.NOT_GELLED
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.GEL_MISSING,
                state=state,
                score=None if state is None else 0.05,
                confidence=None if state is None else 0.8,
                features=features,
                masks=masks,
                message="翻转前未检出凝胶，需人工复核" if before.missing else None,
            )
        assert before.axial is not None and after.axial is not None
        delta_area = abs(after.area - before.area) / before.area * 100
        delta_s = abs(after.axial - before.axial)
        features = features.model_copy(update={"delta_area_pct": delta_area, "delta_s": delta_s})
        values = (delta_area, delta_s, sigma_area or 0.0, sigma_axial or 0.0)
        return self._scored(slot.tube_no, values, features, masks)

    def _scored(
        self,
        tube_no: int,
        values: tuple[float, float, float, float],
        features: GelFeatures,
        masks: TubeMasks,
        state: GelState | None = None,
    ) -> TubeAnalysis:
        """按配置的权重和阈值算评分，给出三态、置信度和评分分项。

        Args:
            tube_no: 管号。
            values: ΔA、Δs、σ_A、σ_s。
            features: 特征。
            masks: 掩码。
            state: 脚本化模式指定的三态，None 时按阈值判定。

        Returns:
            分析结果。
        """
        terms = score_terms(*values, weights=self.weights, limits=self.limits)
        score = float(sum(w * f for w, f in terms.values()))
        high, low = self.thresholds.fully_gelled_min, self.thresholds.not_gelled_max
        return TubeAnalysis(
            tube_no=tube_no,
            status=TubeAnalysisStatus.OK,
            state=state or state_from_score(score, high, low),
            score=score,
            confidence=_confidence(score, high, low),
            features=features,
            masks=masks,
            score_terms={k: ScoreTerm(weight=w, normalized=f) for k, (w, f) in terms.items()},
        )

    @staticmethod
    def _series(
        frames: list[CapturedFrame], geometry: TubeGeometry, config: SynthConfig, *, inverted: bool
    ) -> list[_Measurement]:
        return [_measure(f.image, geometry, config, inverted=inverted) for f in frames]

    @staticmethod
    def _sigmas(
        after: list[_Measurement], before: list[_Measurement]
    ) -> tuple[float | None, float | None]:
        """优先用翻转后的帧序列，凝胶缺失时退回翻转前。"""
        for series in (after, before):
            present = [m for m in series if not m.missing]
            if len(present) >= 2:
                areas = [m.area for m in present]
                axials = [m.axial for m in present if m.axial is not None]
                mean = float(np.mean(areas))
                sigma_area = _std_series(areas)
                return (
                    None if sigma_area is None else sigma_area / mean * 100,
                    _std_series(axials),
                )
        return None, None

    def _scripted(
        self,
        slot: TubeSlot,
        state: GelState,
        geometry: TubeGeometry,
        before: _Measurement,
        after: _Measurement,
        tube_before: Uint8Array,
        tube_after: Uint8Array,
    ) -> TubeAnalysis:
        """按场景给出状态，并生成与状态相符的特征。"""
        rng = np.random.default_rng([self.scenario.seed, slot.tube_no, 3])
        ranges = {
            GelState.FULLY_GELLED: ((0.2, 4.0), (0.001, 0.01), (0.1, 1.0), (0.0005, 0.005)),
            GelState.PARTIALLY_GELLED: ((20.0, 55.0), (0.04, 0.09), (0.5, 3.0), (0.002, 0.01)),
            GelState.NOT_GELLED: ((70.0, 95.0), (0.6, 0.85), (1.0, 4.0), (0.005, 0.02)),
        }[state]
        d_area, d_s, sig_a, sig_s = (float(rng.uniform(*r)) for r in ranges)
        area_before = (
            before.area
            if not before.missing
            else 0.33 * geometry.length * (2 * geometry.inner_radius)
        )
        axial_before = before.axial if before.axial is not None else 0.835
        centroid_before = before.centroid or (
            geometry.cx,
            geometry.y_bottom - 0.165 * geometry.length,
        )
        area_after = area_before * (1 - d_area / 100)
        axial_after = axial_before - d_s if state is not GelState.FULLY_GELLED else axial_before
        features = GelFeatures(
            area_before=area_before,
            area_after=area_after,
            delta_area_pct=d_area,
            centroid_before=centroid_before,
            centroid_after=after.centroid or centroid_before,
            axial_before=axial_before,
            axial_after=axial_after,
            delta_s=d_s,
            sigma_area=sig_a,
            sigma_axial=sig_s,
        )
        masks = TubeMasks(
            tube_before=tube_before,
            tube_after=tube_after,
            gel_before=before.gel,
            gel_after=after.gel,
        )
        return self._scored(slot.tube_no, (d_area, d_s, sig_a, sig_s), features, masks, state)
