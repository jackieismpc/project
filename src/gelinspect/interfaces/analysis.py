"""算法接口：软件只依赖 :class:`GelAnalyzer` 这一个入口。

算法输出三态、评分和特征，**不输出阴阳性**。阴阳性由软件按
:class:`gelinspect.domain.interpretation.GelStateMapping` 得出。
接入说明见 ``docs/algorithm-interface.md``。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Protocol, Self, runtime_checkable

import numpy as np
from pydantic import AwareDatetime, Field, model_validator

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.domain.plate import HOLES_PER_ROW, ROW_COUNT, TUBE_COUNT, tube_to_position
from gelinspect.interfaces._base import BgrImage, BinaryMask, ContractModel

__all__ = [
    "MIN_FRAMES_PER_SIDE",
    "AnalyzerError",
    "AnalyzerInfo",
    "CapturedFrame",
    "GelAnalyzer",
    "GelFeatures",
    "GelState",
    "RowAnalysisRequest",
    "RowAnalysisResult",
    "TubeAnalysis",
    "TubeAnalysisStatus",
    "TubeMasks",
    "TubeSlot",
    "check_row_result",
]

MIN_FRAMES_PER_SIDE = 3
"""翻转前、翻转后各自的最少帧数。"""

_MESSAGE_REQUIRED = frozenset(
    {
        TubeAnalysisStatus.TUBE_NOT_FOUND,
        TubeAnalysisStatus.LOW_QUALITY,
        TubeAnalysisStatus.FAILED,
    }
)
_NO_RESULT = frozenset({TubeAnalysisStatus.TUBE_NOT_FOUND, TubeAnalysisStatus.FAILED})


class AnalyzerError(Exception):
    """算法整体故障，例如模型未加载、模型文件损坏。单支试管的问题不使用此异常。"""


class TubeSlot(ContractModel):
    """一个有样品的管位。

    Attributes:
        tube_no: 管号，1–80。
        position_in_row: 排内位置（孔号），1–8，与管号必须一致。图像中的位置约定见
            ``docs/algorithm-interface.md``。
    """

    tube_no: int = Field(ge=1, le=TUBE_COUNT)
    position_in_row: int = Field(ge=1, le=HOLES_PER_ROW)

    @model_validator(mode="after")
    def _check_position(self) -> Self:
        """校验孔号与管号一致。

        Returns:
            校验后的模型。

        Raises:
            ValueError: 孔号与管号不一致。
        """
        expected = tube_to_position(self.tube_no).hole_index
        if self.position_in_row != expected:
            raise ValueError(f"管号 {self.tube_no} 的孔号应为 {expected}")
        return self

    @classmethod
    def from_tube_no(cls, tube_no: int) -> TubeSlot:
        """由管号构造管位。

        Args:
            tube_no: 管号，1–80。

        Returns:
            孔号已换算好的管位。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        return cls(tube_no=tube_no, position_in_row=tube_to_position(tube_no).hole_index)


class CapturedFrame(ContractModel):
    """一帧抓拍图像。

    Attributes:
        image: H×W×3、uint8、BGR 的整排图像。算法不得修改。
        captured_at: 采集时间，必须带时区。
        source_path: 原图路径，未落盘时为 None。
    """

    image: BgrImage
    captured_at: AwareDatetime
    source_path: Path | None = None


class RowAnalysisRequest(ContractModel):
    """一排的分析请求。

    Attributes:
        experiment_id: 试验标识，仅用于追溯，算法无需理解。
        row_index: 排号，1–10，仅用于追溯，算法无需理解。
        tubes: 本排有样品的管位，1–8 个，都属于 ``row_index``，管号不重复。
        frames_before: 翻转前的帧，至少 3 帧，按时间升序。
        frames_after: 翻转后的帧，至少 3 帧，按时间升序。
        flip_angle_deg: 编码器读到的实际翻转角度，没有编码器时为 None。
        tube_spec: 管型，用于选择标定配置。
        reagent_batch: 鲎试剂批号，用于选择标定配置。
    """

    experiment_id: str = Field(min_length=1)
    row_index: int = Field(ge=1, le=ROW_COUNT)
    tubes: list[TubeSlot] = Field(min_length=1, max_length=HOLES_PER_ROW)
    frames_before: list[CapturedFrame] = Field(min_length=MIN_FRAMES_PER_SIDE)
    frames_after: list[CapturedFrame] = Field(min_length=MIN_FRAMES_PER_SIDE)
    flip_angle_deg: float | None = None
    tube_spec: str | None = None
    reagent_batch: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """校验管位归属、帧尺寸一致和帧时间顺序。

        Returns:
            校验后的模型。

        Raises:
            ValueError: 管号重复或不属于本排、帧尺寸不一致、翻转前的帧晚于翻转后的帧。
        """
        tube_nos = [slot.tube_no for slot in self.tubes]
        if len(set(tube_nos)) != len(tube_nos):
            raise ValueError(f"管号重复：{tube_nos}")
        foreign = [no for no in tube_nos if tube_to_position(no).row_index != self.row_index]
        if foreign:
            raise ValueError(f"管号 {foreign} 不属于第 {self.row_index} 排")
        frames = [*self.frames_before, *self.frames_after]
        if len({frame.image.shape for frame in frames}) != 1:
            raise ValueError("翻转前后所有帧的尺寸必须一致")
        last_before = max(frame.captured_at for frame in self.frames_before)
        first_after = min(frame.captured_at for frame in self.frames_after)
        if last_before > first_after:
            raise ValueError("翻转前的帧不能晚于翻转后的帧")
        return self

    @property
    def image_size(self) -> tuple[int, int]:
        """输入图像的 ``(高, 宽)``，也是所有掩码必须具有的形状。"""
        height, width, _ = self.frames_before[0].image.shape
        return height, width


class AnalyzerInfo(ContractModel):
    """算法版本信息，写入数据库和审计日志。

    Attributes:
        model_version: 模型版本。
        config_version: 阈值和权重配置的版本。
    """

    model_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)


class GelFeatures(ContractModel):
    """凝胶特征。软件只存储和展示，不做计算或补算。

    凝胶缺失时，缺失一侧的面积、质心和轴向位置，以及 ``delta_area_pct``、``delta_s``
    都必须为 None。``sigma_area``、``sigma_axial`` 由算法决定是否给出。

    Attributes:
        area_before: 翻转前凝胶面积，单位像素。
        area_after: 翻转后凝胶面积，单位像素。
        delta_area_pct: 面积变化率 ΔA，单位 %。
        centroid_before: 翻转前凝胶质心 ``(x, y)``，图像坐标，单位像素。
        centroid_after: 翻转后凝胶质心 ``(x, y)``，图像坐标，单位像素。
        axial_before: 翻转前质心的轴向位置，试管坐标系。
        axial_after: 翻转后质心的轴向位置，试管坐标系。
        delta_s: 轴向质心偏移 Δs，试管坐标系，单位待确认。
        sigma_area: 面积的时序标准差 σ_A。
        sigma_axial: 轴向位置的时序标准差 σ_s。
        gel_missing_before: 翻转前是否凝胶缺失。
        gel_missing_after: 翻转后是否凝胶缺失。
    """

    area_before: float | None = Field(default=None, ge=0)
    area_after: float | None = Field(default=None, ge=0)
    delta_area_pct: float | None = None
    centroid_before: tuple[float, float] | None = None
    centroid_after: tuple[float, float] | None = None
    axial_before: float | None = None
    axial_after: float | None = None
    delta_s: float | None = None
    sigma_area: float | None = Field(default=None, ge=0)
    sigma_axial: float | None = Field(default=None, ge=0)
    gel_missing_before: bool = False
    gel_missing_after: bool = False

    @model_validator(mode="after")
    def _check_gel_missing(self) -> Self:
        """校验凝胶缺失时相应字段为 None。

        Returns:
            校验后的模型。

        Raises:
            ValueError: 凝胶缺失时相应字段有值。
        """
        required_none: list[str] = []
        if self.gel_missing_before:
            required_none += ["area_before", "centroid_before", "axial_before"]
        if self.gel_missing_after:
            required_none += ["area_after", "centroid_after", "axial_after"]
        if required_none:
            required_none += ["delta_area_pct", "delta_s"]
        offending = [name for name in required_none if getattr(self, name) is not None]
        if offending:
            raise ValueError(f"凝胶缺失时这些字段必须为 None：{offending}")
        return self


class TubeMasks(ContractModel):
    """一支试管在翻转前后代表帧上的掩码，形状都与原图的高、宽相同。

    Attributes:
        tube_before: 翻转前的试管掩码。
        tube_after: 翻转后的试管掩码。
        gel_before: 翻转前的凝胶掩码，凝胶缺失时为 None。
        gel_after: 翻转后的凝胶掩码，凝胶缺失时为 None。
    """

    tube_before: BinaryMask
    tube_after: BinaryMask
    gel_before: BinaryMask | None = None
    gel_after: BinaryMask | None = None

    @model_validator(mode="after")
    def _check_shapes(self) -> Self:
        """校验四个掩码形状相同。

        Returns:
            校验后的模型。

        Raises:
            ValueError: 掩码形状不一致。
        """
        masks = (self.tube_before, self.tube_after, self.gel_before, self.gel_after)
        if len({mask.shape for mask in masks if mask is not None}) != 1:
            raise ValueError("同一支试管的掩码形状必须一致")
        return self


class TubeAnalysis(ContractModel):
    """一支试管的分析结果。

    各状态的字段约束见 ``docs/algorithm-interface.md``，构造时由校验器强制。

    Attributes:
        tube_no: 管号，与输入对应。
        status: 处理状态。
        state: 凝固三态，无法判定时为 None。
        score: 融合评分，0–1。
        confidence: 置信度，0–1。
        features: 凝胶特征。
        masks: 掩码，用于界面叠加和存档。
        message: 失败原因。
    """

    tube_no: int = Field(ge=1, le=TUBE_COUNT)
    status: TubeAnalysisStatus
    state: GelState | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    features: GelFeatures = Field(default_factory=GelFeatures)
    masks: TubeMasks | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _check_status_fields(self) -> Self:
        """校验状态和其他字段的一致性。

        Returns:
            校验后的模型。

        Raises:
            ValueError: 字段与状态不一致。
        """
        missing = self.features.gel_missing_before or self.features.gel_missing_after
        if missing != (self.status is TubeAnalysisStatus.GEL_MISSING):
            raise ValueError("gel_missing 状态与特征中的缺失标记必须同时出现")
        if self.status is TubeAnalysisStatus.OK and (self.state is None or self.masks is None):
            raise ValueError("ok 状态必须给出 state 和 masks")
        if self.status in _NO_RESULT and (
            self.state is not None
            or self.score is not None
            or self.confidence is not None
            or self.masks is not None
        ):
            raise ValueError(
                f"{self.status.value} 状态下 state、score、confidence、masks 必须为 None"
            )
        if self.status in _MESSAGE_REQUIRED and not self.message:
            raise ValueError(f"{self.status.value} 状态必须给出 message")
        if self.masks is not None:
            if self.features.gel_missing_before and self.masks.gel_before is not None:
                raise ValueError("翻转前凝胶缺失时 gel_before 必须为 None")
            if self.features.gel_missing_after and self.masks.gel_after is not None:
                raise ValueError("翻转后凝胶缺失时 gel_after 必须为 None")
        return self


class RowAnalysisResult(ContractModel):
    """一排的分析结果。

    Attributes:
        analyzer_info: 产生该结果的算法版本。
        elapsed_ms: 算法自测耗时，单位毫秒。
        tubes: 每支试管一条结果，与请求的 ``tubes`` 一一对应。
    """

    analyzer_info: AnalyzerInfo
    elapsed_ms: float = Field(ge=0)
    tubes: list[TubeAnalysis]


@runtime_checkable
class GelAnalyzer(Protocol):
    """算法入口。

    软件在同一条专用工作线程上调用全部方法，并保证不并发，实现无需加锁。
    后端类必须能无参构造，构造时不得加载模型。
    """

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """加载模型并预热，启动时调用一次。

        Args:
            config_path: 算法配置文件路径，为 None 时使用算法默认配置。

        Returns:
            模型和阈值配置的版本。

        Raises:
            AnalyzerError: 模型或配置无法加载。
        """
        ...

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """同步分析一排试管。

        Args:
            request: 一排的分析请求。算法不得修改其中的图像。

        Returns:
            每支试管的结果。单支问题通过 ``status`` 表达，不抛异常。

        Raises:
            AnalyzerError: 尚未 ``load``、已经 ``close``，或发生整体故障。
        """
        ...

    def close(self) -> None:
        """释放模型资源。可重复调用；关闭后可再次 ``load``。"""
        ...


def check_row_result(request: RowAnalysisRequest, result: RowAnalysisResult) -> list[str]:
    """检查结果是否满足只有结合请求才能判断的契约。

    校验器无法检查的内容都在这里：管号一一对应、掩码形状等于原图、掩码取值只有 0 和 255。
    需要遍历像素，契约测试使用；试验引擎如要在运行时调用，应放在算法耗时统计之外。

    Args:
        request: 分析请求。
        result: 算法返回的结果。

    Returns:
        违规描述的列表，为空表示满足契约。
    """
    violations: list[str] = []
    expected = {slot.tube_no for slot in request.tubes}
    counts = Counter(tube.tube_no for tube in result.tubes)
    missing = expected - set(counts)
    extra = set(counts) - expected
    duplicated = {no for no, count in counts.items() if count > 1}
    if missing:
        violations.append(f"缺少管号：{sorted(missing)}")
    if extra:
        violations.append(f"多余管号：{sorted(extra)}")
    if duplicated:
        violations.append(f"重复管号：{sorted(duplicated)}")

    for tube in result.tubes:
        if tube.masks is None:
            continue
        named_masks = {
            "tube_before": tube.masks.tube_before,
            "tube_after": tube.masks.tube_after,
            "gel_before": tube.masks.gel_before,
            "gel_after": tube.masks.gel_after,
        }
        for name, mask in named_masks.items():
            if mask is None:
                continue
            if mask.shape != request.image_size:
                violations.append(
                    f"管号 {tube.tube_no} 的 {name} 形状 {mask.shape} "
                    f"与原图 {request.image_size} 不一致"
                )
            if not np.all((mask == 0) | (mask == 255)):
                violations.append(f"管号 {tube.tube_no} 的 {name} 含有 0 和 255 以外的值")
    return violations
