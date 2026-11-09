"""判读过程：融合评分的分项和三态阈值。

按《凝胶试剂机器人智能检测分割算法方案》第 5 节，算法把四项特征各自归一化到 0–1（越大越凝固），
按权重加权得到融合评分 Score，再与两条阈值比较判为完全凝固、部分凝固或未凝固::

    Score = ω1·f(ΔA) + ω2·f(Δs) + ω3·f(σ_A) + ω4·f(σ_s)
    Score ≥ 完全凝固阈值 → 完全凝固；Score ≤ 未凝固阈值 → 未凝固；其余 → 部分凝固

分项和阈值由算法给出（可选），软件不做计算，只随单管结果的特征快照存档，供界面逐项展示
“测量值 → 归一化 → 权重 → 贡献 → 评分 → 三态 → 阴阳性”的判读过程。本模块定义存档的键，
并从特征快照中读回。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gelinspect.domain.enums import GelState

SCORE_FEATURES: tuple[str, ...] = ("delta_area_pct", "delta_s", "sigma_area", "sigma_axial")
"""参与融合评分的四项特征，与 ``GelFeatures`` 的字段同名，顺序即算法方案中 ω1–ω4 的顺序。"""

WEIGHT_PREFIX = "score_weight."
"""特征快照中权重的键前缀，例如 ``score_weight.delta_area_pct``。"""
TERM_PREFIX = "score_term."
"""特征快照中归一化得分 f(x) 的键前缀。"""
FULLY_GELLED_MIN_KEY = "threshold_fully_gelled_min"
"""特征快照中“完全凝固”阈值的键：Score 不小于它判完全凝固。"""
NOT_GELLED_MAX_KEY = "threshold_not_gelled_max"
"""特征快照中“未凝固”阈值的键：Score 不大于它判未凝固。"""


@dataclass(frozen=True, slots=True)
class ScoreTermView:
    """融合评分中的一项。

    Attributes:
        feature: 特征名，见 :data:`SCORE_FEATURES`。
        weight: 权重 ω。
        normalized: 归一化得分 f(x)，0–1，越大越凝固。
    """

    feature: str
    weight: float
    normalized: float

    @property
    def contribution(self) -> float:
        """这一项对评分的贡献 ω·f(x)。"""
        return self.weight * self.normalized


@dataclass(frozen=True, slots=True)
class Thresholds:
    """三态阈值。

    Attributes:
        not_gelled_max: Score 不大于它判未凝固。
        fully_gelled_min: Score 不小于它判完全凝固。
    """

    not_gelled_max: float
    fully_gelled_min: float

    def __post_init__(self) -> None:
        """校验阈值。

        Raises:
            ValueError: 阈值不在 0–1 内，或未凝固阈值不小于完全凝固阈值。
        """
        if not 0.0 <= self.not_gelled_max < self.fully_gelled_min <= 1.0:
            raise ValueError(
                f"阈值应满足 0 ≤ 未凝固阈值 < 完全凝固阈值 ≤ 1："
                f"{self.not_gelled_max}、{self.fully_gelled_min}"
            )

    def state_for(self, score: float) -> GelState:
        """按阈值判定三态。

        Args:
            score: 融合评分。

        Returns:
            凝固三态。
        """
        if score >= self.fully_gelled_min:
            return GelState.FULLY_GELLED
        if score <= self.not_gelled_max:
            return GelState.NOT_GELLED
        return GelState.PARTIALLY_GELLED


@dataclass(frozen=True, slots=True)
class JudgmentBasis:
    """从特征快照读回的判读过程。

    Attributes:
        terms: 评分分项，按 :data:`SCORE_FEATURES` 的顺序；算法没有给出时为空。
        thresholds: 三态阈值；算法没有给出时为 None。
    """

    terms: tuple[ScoreTermView, ...] = ()
    thresholds: Thresholds | None = None

    @property
    def total(self) -> float | None:
        """各项贡献之和，没有分项时为 None。"""
        return sum(t.contribution for t in self.terms) if self.terms else None


def flatten_judgment(
    terms: Mapping[str, tuple[float, float]] | None,
    thresholds: Thresholds | None,
) -> dict[str, float]:
    """把分项和阈值展开成特征快照里的键值，便于随结果存档。

    Args:
        terms: 特征名到 ``(权重, 归一化得分)``，None 表示算法没有给出。
        thresholds: 三态阈值，None 表示算法没有给出。

    Returns:
        可以合并进特征快照的字典。
    """
    flat: dict[str, float] = {}
    for feature, (weight, normalized) in (terms or {}).items():
        flat[WEIGHT_PREFIX + feature] = float(weight)
        flat[TERM_PREFIX + feature] = float(normalized)
    if thresholds is not None:
        flat[NOT_GELLED_MAX_KEY] = thresholds.not_gelled_max
        flat[FULLY_GELLED_MIN_KEY] = thresholds.fully_gelled_min
    return flat


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def read_judgment(features: Mapping[str, object]) -> JudgmentBasis:
    """从单管结果的特征快照读回判读过程。缺项或格式不对的部分按“没有给出”处理。

    Args:
        features: 单管结果的特征快照。

    Returns:
        判读过程。
    """
    terms: list[ScoreTermView] = []
    for feature in SCORE_FEATURES:
        weight = _number(features.get(WEIGHT_PREFIX + feature))
        normalized = _number(features.get(TERM_PREFIX + feature))
        if weight is not None and normalized is not None:
            terms.append(ScoreTermView(feature, weight, normalized))
    thresholds: Thresholds | None = None
    low = _number(features.get(NOT_GELLED_MAX_KEY))
    high = _number(features.get(FULLY_GELLED_MIN_KEY))
    if low is not None and high is not None:
        try:
            thresholds = Thresholds(low, high)
        except ValueError:
            thresholds = None
    return JudgmentBasis(tuple(terms), thresholds)
