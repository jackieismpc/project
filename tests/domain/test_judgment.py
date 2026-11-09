"""判读过程：分项和阈值的存档与读回。"""

from __future__ import annotations

import pytest

from gelinspect.domain.enums import GelState
from gelinspect.domain.judgment import (
    FULLY_GELLED_MIN_KEY,
    NOT_GELLED_MAX_KEY,
    SCORE_FEATURES,
    Thresholds,
    flatten_judgment,
    read_judgment,
)


def test_flatten_and_read_back_in_feature_order() -> None:
    terms = {"sigma_axial": (0.1, 1.0), "delta_area_pct": (0.5, 0.2), "delta_s": (0.3, 0.5)}
    flat = flatten_judgment(terms, Thresholds(0.25, 0.85))
    basis = read_judgment({**flat, "delta_area_pct": 12.0})
    assert [t.feature for t in basis.terms] == ["delta_area_pct", "delta_s", "sigma_axial"]
    assert basis.total == pytest.approx(0.5 * 0.2 + 0.3 * 0.5 + 0.1)
    assert basis.thresholds == Thresholds(0.25, 0.85)


def test_missing_or_malformed_values_read_as_not_given() -> None:
    assert read_judgment({}).terms == () and read_judgment({}).total is None
    broken = {
        "score_weight.delta_s": True,
        "score_term.delta_s": 0.5,
        NOT_GELLED_MAX_KEY: 0.9,
        FULLY_GELLED_MIN_KEY: 0.2,
    }
    basis = read_judgment(broken)
    assert basis.terms == () and basis.thresholds is None


@pytest.mark.parametrize(
    ("score", "state"),
    [
        (0.0, GelState.NOT_GELLED),
        (0.25, GelState.NOT_GELLED),
        (0.26, GelState.PARTIALLY_GELLED),
        (0.84, GelState.PARTIALLY_GELLED),
        (0.85, GelState.FULLY_GELLED),
        (1.0, GelState.FULLY_GELLED),
    ],
)
def test_thresholds_include_their_boundaries(score: float, state: GelState) -> None:
    assert Thresholds(0.25, 0.85).state_for(score) is state


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="阈值"):
        Thresholds(0.85, 0.25)


def test_score_features_follow_the_algorithm_plan() -> None:
    assert SCORE_FEATURES == ("delta_area_pct", "delta_s", "sigma_area", "sigma_axial")
