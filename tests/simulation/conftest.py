"""模拟数据测试的公共构造函数。"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from numpy.typing import NDArray

from gelinspect.domain.enums import GelState
from gelinspect.domain.plate import tubes_in_row
from gelinspect.interfaces.analysis import CapturedFrame, RowAnalysisRequest, TubeSlot
from simulation.image_synth import SynthConfig, SynthFrame, TubeSpec, render_pair, render_row

S = GelState
SMALL = SynthConfig(width=960, height=540)
"""测试默认使用的小分辨率，掩码和算法逻辑与大分辨率一致。"""


def frames_from(image: NDArray[np.uint8], start: datetime, count: int = 3) -> list[CapturedFrame]:
    return [
        CapturedFrame(image=image, captured_at=start + timedelta(milliseconds=100 * n))
        for n in range(count)
    ]


def request_from(
    before: SynthFrame,
    after: SynthFrame,
    *,
    row_index: int = 1,
    positions: Sequence[int] = tuple(range(1, 9)),
    after_frames: Sequence[SynthFrame] | None = None,
) -> RowAnalysisRequest:
    start = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    after_list = list(after_frames) if after_frames else [after, after, after]
    return RowAnalysisRequest(
        experiment_id="sim-test",
        row_index=row_index,
        tubes=[TubeSlot.from_tube_no(tubes_in_row(row_index)[p - 1]) for p in positions],
        frames_before=frames_from(before.image, start),
        frames_after=[
            CapturedFrame(image=f.image, captured_at=start + timedelta(seconds=10, milliseconds=n))
            for n, f in enumerate(after_list)
        ],
        flip_angle_deg=180.0,
    )


@pytest.fixture
def mixed_specs() -> list[TubeSpec | None]:
    from simulation.image_synth import specs_from_states

    return specs_from_states(
        [S.FULLY_GELLED] * 3 + [S.PARTIALLY_GELLED] * 3 + [S.NOT_GELLED] * 2, 3
    )


@pytest.fixture
def mixed_request(
    mixed_specs: list[TubeSpec | None],
) -> tuple[RowAnalysisRequest, SynthFrame, SynthFrame]:
    before, after = render_pair(mixed_specs, SMALL, seed=1)
    return request_from(before, after), before, after


__all__ = ["SMALL", "S", "render_row", "request_from"]
