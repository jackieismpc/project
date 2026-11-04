"""合成一排 8 支试管的背光图像和真值掩码。

画面模型（与算法方案的图像约定一致）：

- 明亮的背光底，边缘略暗；一排 8 支竖直试管，``position_in_row=1`` 在最左侧。
- 试管透明，管壁折射形成较暗的边线；管内有凝胶或液体，带高光条、管口边沿和噪声。
- 翻转前管口朝上、管底朝下；翻转后绕管中心转 180°，管口朝下，试管占据的区域不变。
- 管内样品团的透光率低于空管，因此阈值分割即可得到样品团。

三种状态的样品团（均在试管坐标系中定义，原点在管口，轴向 ``s`` 指向管底）：

- 完全凝固：凝胶仍贴着管底，仅有轻微下沉或倾斜。
- 部分凝固：管底保留一部分凝胶，另一部分滑向管口，形成一个分离的小团。
- 未凝固：整管为液体，翻转后只在管口附近留下一小团，其余沿管壁铺开，透光率接近空管。

所有函数都是纯函数：相同的参数和随机种子产生相同的像素。分辨率由 :class:`SynthConfig`
配置，默认 1920×1080，可以到 4000×3000。

可以直接运行本模块，生成翻转前后的拼图预览::

    uv run python -m simulation.image_synth --out simulation/generated/preview.png
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.domain.enums import GelState

SLOT_COUNT = 8
"""一排的管位数。"""
GEL_VISIBLE_TRANSMITTANCE = 0.88
"""样品团相对背光的透光率低于该值才会被当作可见的凝胶。"""
_WALL_TRANSMITTANCE = 0.68
_EMPTY_TRANSMITTANCE = 0.97
_MIN_SCALE = 0.03
_MOUTH_EXCLUDE = 0.035
"""管口附近（轴向比例）有边沿线，分割时排除。"""

Uint8Array = NDArray[np.uint8]
FloatArray = NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class SynthConfig:
    """合成参数。

    Attributes:
        width: 图像宽度，像素。
        height: 图像高度，像素。
        backlight: 背光亮度，0–255。
        noise_sigma: 高斯噪声标准差，灰度值。
        blur_sigma: 试管区域的柔化半径，像素，0 表示不柔化。
        reflections: 是否绘制反光。
        tube_radius_frac: 管外半径占管位间距（宽度/8）的比例。
        tube_length_frac: 管长占图像高度的比例。
    """

    width: int = 1920
    height: int = 1080
    backlight: float = 235.0
    noise_sigma: float = 2.5
    blur_sigma: float = 0.7
    reflections: bool = True
    tube_radius_frac: float = 0.17
    tube_length_frac: float = 0.62

    @property
    def pitch(self) -> float:
        """相邻管位的中心距，像素。"""
        return self.width / SLOT_COUNT


@dataclass(frozen=True, slots=True)
class TubeGeometry:
    """一支试管在图像中的几何（翻转前的姿态）。

    Attributes:
        position: 排内位置，1–8。
        cx: 管中心线的 x 坐标。
        y_top: 管口的 y 坐标。
        y_bottom: 管底外沿的 y 坐标。
        radius: 外半径。
        wall: 管壁厚度。
    """

    position: int
    cx: float
    y_top: float
    y_bottom: float
    radius: float
    wall: float

    @property
    def length(self) -> float:
        """管长。"""
        return self.y_bottom - self.y_top

    @property
    def center_y(self) -> float:
        """管中心的 y 坐标，翻转的转轴位置。"""
        return (self.y_top + self.y_bottom) / 2

    @property
    def inner_radius(self) -> float:
        """内半径。"""
        return self.radius - self.wall


def slot_geometry(config: SynthConfig, position: int) -> TubeGeometry:
    """计算某个管位的几何。

    Args:
        config: 合成参数。
        position: 排内位置，1–8。

    Returns:
        该管位的几何。

    Raises:
        ValueError: 位置超出 1–8。
    """
    if not 1 <= position <= SLOT_COUNT:
        raise ValueError(f"位置应在 1–{SLOT_COUNT} 内：{position}")
    radius = config.tube_radius_frac * config.pitch
    length = config.tube_length_frac * config.height
    y_top = (config.height - length) / 2
    return TubeGeometry(
        position=position,
        cx=(position - 0.5) * config.pitch,
        y_top=y_top,
        y_bottom=y_top + length,
        radius=radius,
        wall=max(2.0, 0.12 * radius),
    )


@dataclass(frozen=True, slots=True)
class TubeSpec:
    """一支试管的内容。

    Attributes:
        state: 凝固状态；None 表示该管位没有试管。
        fill_fraction: 样品高度占管长的比例。
        remain_fraction: 部分凝固时，翻转后管底保留的凝胶比例。
        pool_fraction: 未凝固时，翻转后管口处可见液团相对原样品高度的比例，0 表示不可见。
        tilt: 翻转后凝胶表面的倾斜程度，-1 到 1。
        gel_transmittance: 凝胶的透光率。
        liquid_transmittance: 液体的透光率。
    """

    state: GelState | None
    fill_fraction: float = 0.33
    remain_fraction: float = 0.6
    pool_fraction: float = 0.2
    tilt: float = 0.0
    gel_transmittance: float = 0.55
    liquid_transmittance: float = 0.70


def sample_tube_spec(state: GelState | None, rng: np.random.Generator) -> TubeSpec:
    """按状态随机抽取一组合理的试管参数。

    Args:
        state: 凝固状态，None 表示空管位。
        rng: 随机数生成器，抽取顺序固定，结果可复现。

    Returns:
        试管内容。
    """
    return TubeSpec(
        state=state,
        fill_fraction=float(rng.uniform(0.28, 0.36)),
        remain_fraction=float(rng.uniform(0.45, 0.75)),
        pool_fraction=float(rng.uniform(0.08, 0.30)),
        tilt=float(rng.uniform(-1.0, 1.0)),
        gel_transmittance=float(rng.uniform(0.50, 0.60)),
        liquid_transmittance=float(rng.uniform(0.66, 0.74)),
    )


def specs_from_states(states: Sequence[GelState | None], seed: int = 0) -> list[TubeSpec | None]:
    """为一排的状态列表生成试管内容，补足到 8 个管位。

    Args:
        states: 从位置 1 起的状态，None 表示空管位。
        seed: 随机种子。

    Returns:
        长度为 8 的列表，空管位为 None。

    Raises:
        ValueError: 状态多于 8 个。
    """
    if len(states) > SLOT_COUNT:
        raise ValueError(f"一排最多 {SLOT_COUNT} 个管位：{len(states)}")
    rng = np.random.default_rng(seed)
    result: list[TubeSpec | None] = []
    for position in range(SLOT_COUNT):
        state = states[position] if position < len(states) else None
        spec = sample_tube_spec(state, rng)
        result.append(spec if state is not None else None)
    return result


@dataclass(frozen=True, slots=True)
class _Band:
    """试管坐标系中的一段样品团：``s_lo <= s <= s_hi`` 的区域。"""

    s_lo: float
    s_hi: float
    tilt_px: float
    lateral: float
    transmittance: float


def _mix(first: _Band, second: _Band, ratio: float) -> _Band:
    """在两个样品团之间线性插值。"""

    def lerp(a: float, b: float) -> float:
        return a + (b - a) * ratio

    return _Band(
        s_lo=lerp(first.s_lo, second.s_lo),
        s_hi=lerp(first.s_hi, second.s_hi),
        tilt_px=lerp(first.tilt_px, second.tilt_px),
        lateral=lerp(first.lateral, second.lateral),
        transmittance=lerp(first.transmittance, second.transmittance),
    )


def _bands(
    spec: TubeSpec, geometry: TubeGeometry
) -> tuple[tuple[_Band, _Band], tuple[_Band, _Band]]:
    """给出翻转前后各两个样品团（主体和分离的小团）。

    Args:
        spec: 试管内容。
        geometry: 试管几何。

    Returns:
        ``((前主体, 前小团), (后主体, 后小团))``。
    """
    length = geometry.length
    end = length + geometry.radius
    height = spec.fill_fraction * length
    surface = length - height
    first_t = (
        spec.liquid_transmittance if spec.state is GelState.NOT_GELLED else spec.gel_transmittance
    )
    before_main = _Band(surface, end, 0.0, 1.0, first_t)
    before_blob = _Band(surface, surface, 0.0, 0.75, spec.gel_transmittance)
    after_main = before_main
    after_blob = before_blob
    if spec.state is GelState.FULLY_GELLED:
        after_main = _Band(surface + 0.02 * height, end, spec.tilt * 0.08 * height, 1.0, first_t)
    elif spec.state is GelState.PARTIALLY_GELLED:
        kept = spec.remain_fraction * height
        blob_height = 0.5 * (height - kept)
        blob_lo = length - kept - 0.10 * length - blob_height
        after_main = _Band(length - kept, end, spec.tilt * 0.35 * height, 1.0, first_t)
        after_blob = _Band(blob_lo, blob_lo + blob_height, 0.0, 0.75, spec.gel_transmittance)
    elif spec.state is GelState.NOT_GELLED:
        mouth = 0.06 * length
        after_main = _Band(mouth, mouth + spec.pool_fraction * height, 0.0, 1.0, first_t)
        after_blob = _Band(mouth, mouth, 0.0, 0.75, spec.gel_transmittance)
    return (before_main, before_blob), (after_main, after_blob)


def capsule_sdf(u: FloatArray, s: FloatArray, radius: float, length: float) -> FloatArray:
    """顶端平口、底端半圆的圆管的有符号距离，负值在内部。

    Args:
        u: 横向坐标（相对管中心线）。
        s: 轴向坐标（管口为 0，向管底为正）。
        radius: 半径。
        length: 管长（管口到管底外沿）。

    Returns:
        与输入同形状的有符号距离，单位像素。
    """
    centre = length - radius
    straight = np.abs(u) - radius
    rounded = np.hypot(u, s - centre) - radius
    distance = np.where(s <= centre, straight, rounded)
    return np.maximum(distance, -s).astype(np.float32)


def tube_frame(
    geometry: TubeGeometry, box: tuple[int, int, int, int], scale: float
) -> tuple[FloatArray, FloatArray]:
    """计算感兴趣区域内每个像素的试管坐标 ``(u, s)``。

    ``scale`` 为 1 表示翻转前（管口朝上），-1 表示翻转后（管口朝下），中间值对应
    翻转过程中绕管中心的透视压缩。

    Args:
        geometry: 试管几何。
        box: ``(x0, x1, y0, y1)``，右开区间。
        scale: 轴向缩放，``cos`` 翻转角，非零。

    Returns:
        ``(u, s)``，形状均为 ``(y1 - y0, x1 - x0)``。
    """
    x0, x1, y0, y1 = box
    xs = np.arange(x0, x1, dtype=np.float32) - np.float32(geometry.cx)
    ys = np.arange(y0, y1, dtype=np.float32)
    axial = geometry.length / 2 + (ys - np.float32(geometry.center_y)) / np.float32(scale)
    u, s = np.broadcast_arrays(xs[None, :], axial[:, None])
    return u.astype(np.float32), s.astype(np.float32)


def tube_box(
    geometry: TubeGeometry, config: SynthConfig, margin: float = 0.5
) -> tuple[int, int, int, int]:
    """试管的感兴趣区域，包含 ``margin`` 倍半径的边距。

    Args:
        geometry: 试管几何。
        config: 合成参数。
        margin: 边距，单位为外半径的倍数。

    Returns:
        ``(x0, x1, y0, y1)``，右开区间，已裁剪到图像内。
    """
    pad = margin * geometry.radius + 4
    x0 = max(0, int(np.floor(geometry.cx - geometry.radius - pad)))
    x1 = min(config.width, int(np.ceil(geometry.cx + geometry.radius + pad)))
    y0 = max(0, int(np.floor(geometry.y_top - pad)))
    y1 = min(config.height, int(np.ceil(geometry.y_bottom + pad)))
    return x0, x1, y0, y1


def _to_mask(condition: NDArray[np.bool_]) -> Uint8Array:
    """布尔数组转 0/255 掩码。"""
    mask = np.zeros(condition.shape, dtype=np.uint8)
    mask[condition] = 255
    return mask


def largest_component(mask: Uint8Array) -> Uint8Array:
    """保留最大连通域。

    Args:
        mask: 0/255 的二值掩码。

    Returns:
        只含最大连通域的 0/255 掩码，输入为空时返回全零。
    """
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8))
    if count <= 1:
        return np.zeros_like(mask)
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return _to_mask(np.asarray(labels == best))


@dataclass(slots=True)
class SynthFrame:
    """一帧合成图及其真值。

    Attributes:
        image: H×W×3、uint8、BGR 的整排图像。
        tube_labels: H×W，值为管位号（1–8），0 为背景；不生成真值时为 None。
        gel_labels: H×W，值为管位号，表示可见的样品团；不生成真值时为 None。
    """

    image: Uint8Array
    tube_labels: Uint8Array | None = None
    gel_labels: Uint8Array | None = None

    def tube_mask(self, position: int) -> Uint8Array:
        """某个管位的试管真值掩码。

        Args:
            position: 排内位置，1–8。

        Returns:
            H×W 的 0/255 掩码。

        Raises:
            ValueError: 渲染时没有生成真值。
        """
        if self.tube_labels is None:
            raise ValueError("该帧没有真值")
        return _to_mask(self.tube_labels == position)

    def gel_mask(self, position: int, *, largest_only: bool = False) -> Uint8Array:
        """某个管位的样品团真值掩码。

        Args:
            position: 排内位置，1–8。
            largest_only: 为真时只保留最大连通域，与算法的特征口径一致。

        Returns:
            H×W 的 0/255 掩码。

        Raises:
            ValueError: 渲染时没有生成真值。
        """
        if self.gel_labels is None:
            raise ValueError("该帧没有真值")
        mask = _to_mask(self.gel_labels == position)
        return largest_component(mask) if largest_only else mask


def _background(config: SynthConfig) -> Uint8Array:
    """生成带暗角的背光底，BGR。"""
    fx = (np.arange(config.width, dtype=np.float32) / (config.width - 1) - 0.5) * 2
    fy = (np.arange(config.height, dtype=np.float32) / (config.height - 1) - 0.5) * 2
    falloff = 1.0 - 0.10 * fx[None, :] ** 2 - 0.06 * fy[:, None] ** 2
    image = np.empty((config.height, config.width, 3), dtype=np.uint8)
    for channel, gain in enumerate((0.94, 0.98, 1.0)):
        image[:, :, channel] = np.clip(config.backlight * gain * falloff, 0, 255).astype(np.uint8)
    return image


def _smooth_step(value: float) -> float:
    """三次平滑插值，输入限制在 0–1。"""
    x = min(max(value, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _band_coverage(u: FloatArray, s: FloatArray, band: _Band, inner_radius: float) -> FloatArray:
    """样品团在每个像素上的覆盖率（0–1，未乘管内覆盖率）。"""
    if band.s_hi - band.s_lo < 1.0:
        return np.zeros_like(u)
    ratio = u / np.float32(inner_radius)
    meniscus = np.float32(0.02 * inner_radius * 2)
    low = band.s_lo + band.tilt_px * ratio - meniscus * ratio**2
    high = band.s_hi + meniscus * ratio**2
    coverage = np.clip(s - low + 0.5, 0, 1) * np.clip(high - s + 0.5, 0, 1)
    coverage = coverage * np.clip(band.lateral * inner_radius - np.abs(u) + 0.5, 0, 1)
    return np.asarray(coverage, dtype=np.float32)


def _highlights(
    u: FloatArray,
    s: FloatArray,
    yy: FloatArray,
    geometry: TubeGeometry,
    scale: float,
) -> FloatArray:
    """反光：两条竖直高光和管底的光斑，在图像坐标中固定（光源不随试管翻转）。"""
    radius = geometry.inner_radius
    length = geometry.length
    rel = (yy - np.float32(geometry.center_y)) / np.float32(length)
    envelope = np.clip((0.40 + rel) * 12, 0, 1) * np.clip((0.32 - rel) * 12, 0, 1)
    streak = 28 * np.exp(-(((u + 0.52 * radius) / (0.07 * radius)) ** 2)) * envelope
    streak += 14 * np.exp(-(((u - 0.66 * radius) / (0.04 * radius)) ** 2)) * envelope
    bottom_s = length - geometry.radius * 0.8
    glint = 40 * np.exp(
        -((u + 0.35 * radius) ** 2 + (s - bottom_s) ** 2) / np.float32((0.18 * radius) ** 2)
    )
    return np.asarray(streak + glint * (1 if scale > 0 else 0.6), dtype=np.float32)


def _draw_tube(
    image: Uint8Array,
    config: SynthConfig,
    spec: TubeSpec,
    geometry: TubeGeometry,
    *,
    flip_phase: float,
    settle: float,
    tube_labels: Uint8Array | None,
    gel_labels: Uint8Array | None,
) -> None:
    """把一支试管画进图像，并更新真值标签。"""
    scale = float(np.cos(np.pi * flip_phase))
    if abs(scale) < _MIN_SCALE:
        scale = _MIN_SCALE if scale >= 0 else -_MIN_SCALE
    box = tube_box(geometry, config)
    x0, x1, y0, y1 = box
    u, s = tube_frame(geometry, box, scale)
    yy = np.broadcast_to(np.arange(y0, y1, dtype=np.float32)[:, None], u.shape)
    length = geometry.length

    outer = np.clip(0.5 - capsule_sdf(u, s, geometry.radius, length), 0, 1)
    inner = np.clip(0.5 - capsule_sdf(u, s, geometry.inner_radius, length), 0, 1)
    wall = np.clip(outer - inner, 0, 1)

    before, after = _bands(spec, geometry)
    ratio = _smooth_step(settle)
    mass_t = np.ones_like(u)
    gel_cover = np.zeros_like(u)
    for first, second in zip(before, after, strict=True):
        band = _mix(first, second, ratio)
        cover = _band_coverage(u, s, band, geometry.inner_radius) * inner
        mass_t *= 1 - cover * (1 - band.transmittance)
        if band.transmittance < GEL_VISIBLE_TRANSMITTANCE:
            gel_cover = np.maximum(gel_cover, cover)

    edge = (np.abs(u) / np.float32(geometry.inner_radius)) ** 6
    rim = outer * np.clip(1 - np.abs(s - 0.01 * length) / (0.012 * length), 0, 1)
    transmit = (
        1
        - wall * (1 - _WALL_TRANSMITTANCE)
        - inner * (1 - _EMPTY_TRANSMITTANCE * mass_t * (1 - 0.08 * np.clip(edge, 0, 1)))
        - 0.25 * rim
    )
    region = image[y0:y1, x0:x1].astype(np.float32) * transmit[:, :, None]
    if config.reflections:
        region += (_highlights(u, s, yy, geometry, scale) * inner)[:, :, None]
    if config.blur_sigma > 0:
        region = cv2.GaussianBlur(region, (0, 0), config.blur_sigma)
    image[y0:y1, x0:x1] = np.clip(region + 0.5, 0, 255).astype(np.uint8)

    if tube_labels is not None and gel_labels is not None:
        tube_labels[y0:y1, x0:x1][outer >= 0.5] = geometry.position
        gel_labels[y0:y1, x0:x1][gel_cover >= 0.5] = geometry.position


def add_noise(image: Uint8Array, sigma: float, rng: np.random.Generator) -> None:
    """分块加高斯噪声，避免一次性分配整幅浮点图。"""
    if sigma <= 0:
        return
    step = 256
    for top in range(0, image.shape[0], step):
        block = image[top : top + step]
        noise = rng.standard_normal(block.shape, dtype=np.float32) * np.float32(sigma)
        block[:] = np.clip(block.astype(np.float32) + noise + 0.5, 0, 255).astype(np.uint8)


def render_row(
    specs: Sequence[TubeSpec | None],
    config: SynthConfig | None = None,
    *,
    flip_phase: float = 0.0,
    settle: float = 0.0,
    seed: int = 0,
    truth: bool = True,
) -> SynthFrame:
    """合成一排的一帧。

    Args:
        specs: 从位置 1 起的试管内容，None 或 ``state=None`` 表示空管位，不足 8 个时补空。
        config: 合成参数，默认 1920×1080。
        flip_phase: 翻转进度，0 为翻转前，1 为翻转后，中间为翻转过程。
        settle: 样品团的流动进度，0 为翻转前的分布，1 为翻转后稳定的分布。
        seed: 噪声的随机种子。
        truth: 是否生成真值标签。

    Returns:
        合成帧。

    Raises:
        ValueError: 管位多于 8 个，或进度超出 0–1。
    """
    config = config or SynthConfig()
    if len(specs) > SLOT_COUNT:
        raise ValueError(f"一排最多 {SLOT_COUNT} 个管位：{len(specs)}")
    if not (0.0 <= flip_phase <= 1.0 and 0.0 <= settle <= 1.0):
        raise ValueError("flip_phase 和 settle 必须在 0–1 内")
    image = _background(config)
    tube_labels = np.zeros((config.height, config.width), dtype=np.uint8) if truth else None
    gel_labels = np.zeros((config.height, config.width), dtype=np.uint8) if truth else None
    for position, spec in enumerate(specs, start=1):
        if spec is None or spec.state is None:
            continue
        _draw_tube(
            image,
            config,
            spec,
            slot_geometry(config, position),
            flip_phase=flip_phase,
            settle=settle,
            tube_labels=tube_labels,
            gel_labels=gel_labels,
        )
    add_noise(image, config.noise_sigma, np.random.default_rng(seed))
    return SynthFrame(image=image, tube_labels=tube_labels, gel_labels=gel_labels)


def render_pair(
    specs: Sequence[TubeSpec | None], config: SynthConfig | None = None, *, seed: int = 0
) -> tuple[SynthFrame, SynthFrame]:
    """合成翻转前和翻转后（样品团已稳定）的一对帧。

    Args:
        specs: 同 :func:`render_row`。
        config: 合成参数。
        seed: 随机种子，前后帧的噪声互相独立。

    Returns:
        ``(翻转前, 翻转后)``。
    """
    before = render_row(specs, config, seed=seed)
    after = render_row(specs, config, flip_phase=1.0, settle=1.0, seed=seed + 1)
    return before, after


def main(argv: Sequence[str] | None = None) -> None:
    """命令行入口：生成翻转前后的拼图，上方为翻转前，下方为翻转后。

    Args:
        argv: 命令行参数，默认读取 ``sys.argv``。
    """
    parser = argparse.ArgumentParser(description="合成整排试管图并拼接翻转前后")
    parser.add_argument("--out", type=Path, default=Path("simulation/generated/preview.png"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    states = [
        GelState.FULLY_GELLED,
        GelState.FULLY_GELLED,
        GelState.PARTIALLY_GELLED,
        GelState.PARTIALLY_GELLED,
        GelState.NOT_GELLED,
        GelState.NOT_GELLED,
        GelState.FULLY_GELLED,
        GelState.NOT_GELLED,
    ]
    config = replace(SynthConfig(), width=args.width, height=args.height)
    before, after = render_pair(specs_from_states(states, args.seed), config, seed=args.seed)
    gap = np.full((8, config.width, 3), 128, dtype=np.uint8)
    sheet = np.vstack([before.image, gap, after.image])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), sheet)


if __name__ == "__main__":
    main()
