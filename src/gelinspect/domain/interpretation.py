"""算法三态到阴阳性的映射。

算法只输出三态，阴阳性由软件按本模块的规则得出。三态各自的映射可以配置；
无法判定（状态不可信或 ``state`` 为空）固定为待复核，不开放配置，
以保证失败的试管绝不会被默认判为阴性或阳性。

低置信度阈值默认不启用（PLAN 第 7 节第 17 项等待 QA 确认）。启用后，置信度低于阈值或算法没有给出
置信度的结果转待复核；阈值只会把结果转去人工，不会改变阴阳性。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gelinspect.domain.enums import GelState, Interpretation, TubeAnalysisStatus

_TRUSTED_STATUSES = frozenset({TubeAnalysisStatus.OK, TubeAnalysisStatus.GEL_MISSING})
"""``state`` 可以直接用于判读的算法状态。"""


@dataclass(frozen=True, slots=True)
class GelStateMapping:
    """三态到阴阳性的映射规则，默认值即默认规则。

    Attributes:
        fully_gelled: 完全凝固的判读，默认阳性。
        partially_gelled: 部分凝固的判读，默认待复核。
        not_gelled: 未凝固的判读，默认阴性。
        low_confidence_threshold: 低置信度阈值（0–1），None 表示不启用。
    """

    fully_gelled: Interpretation = Interpretation.POSITIVE
    partially_gelled: Interpretation = Interpretation.PENDING_REVIEW
    not_gelled: Interpretation = Interpretation.NEGATIVE
    low_confidence_threshold: float | None = None

    def __post_init__(self) -> None:
        """校验阈值范围。

        Raises:
            ValueError: 阈值不在 0–1 内。
        """
        threshold = self.low_confidence_threshold
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise ValueError(f"低置信度阈值应在 0–1 内：{threshold}")

    @classmethod
    def from_config(
        cls, config: Mapping[str, str], *, low_confidence_threshold: float | None = None
    ) -> GelStateMapping:
        """从配置构造映射，未出现的状态使用默认值。

        Args:
            config: 键为 :class:`GelState` 的值，值为 :class:`Interpretation` 的值，
                例如 ``{"partially_gelled": "positive"}``。
            low_confidence_threshold: 低置信度阈值，None 表示不启用。

        Returns:
            映射规则。

        Raises:
            ValueError: 配置中有未知的状态或判读值，或阈值不在 0–1 内。
        """
        parsed = {GelState(key).value: Interpretation(value) for key, value in config.items()}
        return cls(**parsed, low_confidence_threshold=low_confidence_threshold)

    def to_config(self) -> dict[str, str]:
        """导出为可写入配置或审计日志的字典。

        Returns:
            键为三态的值，值为判读的值；启用低置信度阈值时另有 ``low_confidence_threshold``。
        """
        config = {state.value: self.for_state(state).value for state in GelState}
        if self.low_confidence_threshold is not None:
            config["low_confidence_threshold"] = f"{self.low_confidence_threshold:g}"
        return config

    def for_state(self, state: GelState) -> Interpretation:
        """查询某一态的判读。

        Args:
            state: 凝固状态。

        Returns:
            该状态对应的判读。
        """
        match state:
            case GelState.FULLY_GELLED:
                return self.fully_gelled
            case GelState.PARTIALLY_GELLED:
                return self.partially_gelled
            case GelState.NOT_GELLED:
                return self.not_gelled

    def is_low_confidence(self, confidence: float | None) -> bool:
        """置信度是否低于阈值。

        Args:
            confidence: 算法置信度，None 表示算法没有给出。

        Returns:
            启用阈值且置信度低于阈值或缺失时为 True；不启用时总是 False。
        """
        threshold = self.low_confidence_threshold
        if threshold is None:
            return False
        return confidence is None or confidence < threshold

    def interpret(
        self, status: TubeAnalysisStatus, state: GelState | None, confidence: float | None = None
    ) -> Interpretation:
        """根据算法输出得出判读。

        只有 ``ok`` 和 ``gel_missing`` 且 ``state`` 非空时才使用映射规则，
        其余情况（包括带 ``state`` 的 ``low_quality``）都是待复核。启用低置信度阈值时，
        可信的结果若低于阈值也是待复核。

        Args:
            status: 算法状态。
            state: 算法三态，无法判定时为 None。
            confidence: 算法置信度。

        Returns:
            判读结果。
        """
        if state is None or status not in _TRUSTED_STATUSES:
            return Interpretation.PENDING_REVIEW
        if self.is_low_confidence(confidence):
            return Interpretation.PENDING_REVIEW
        return self.for_state(state)
