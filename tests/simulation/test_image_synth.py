"""图像合成器：可复现、掩码对齐、三态的面积和位置关系。"""

import numpy as np

from gelinspect.domain.enums import GelState
from simulation.image_synth import (
    SLOT_COUNT,
    SynthConfig,
    TubeSpec,
    largest_component,
    render_pair,
    render_row,
    slot_geometry,
    specs_from_states,
)
from tests.simulation.conftest import SMALL, S


def test_same_seed_gives_identical_pixels() -> None:
    specs = specs_from_states([S.FULLY_GELLED, S.PARTIALLY_GELLED, S.NOT_GELLED], 5)
    first = render_row(specs, SMALL, seed=9)
    second = render_row(specs, SMALL, seed=9)
    assert np.array_equal(first.image, second.image)
    assert first.tube_labels is not None and second.tube_labels is not None
    assert np.array_equal(first.tube_labels, second.tube_labels)


def test_different_seed_changes_noise_only() -> None:
    specs = specs_from_states([S.FULLY_GELLED], 5)
    first = render_row(specs, SMALL, seed=1)
    second = render_row(specs, SMALL, seed=2)
    assert not np.array_equal(first.image, second.image)
    assert first.tube_labels is not None and second.tube_labels is not None
    assert np.array_equal(first.tube_labels, second.tube_labels)


def test_image_format() -> None:
    frame = render_row(specs_from_states([S.FULLY_GELLED] * 8), SMALL)
    assert frame.image.shape == (540, 960, 3)
    assert frame.image.dtype == np.uint8
    assert frame.tube_labels is not None
    assert set(np.unique(frame.tube_labels)) == {0, *range(1, SLOT_COUNT + 1)}


def test_resolution_is_configurable_up_to_4000x3000() -> None:
    big = SynthConfig(width=4000, height=3000)
    frame = render_row(specs_from_states([S.PARTIALLY_GELLED] * 8), big, truth=False)
    assert frame.image.shape == (3000, 4000, 3)
    assert frame.tube_labels is None


def test_empty_slots_have_no_tube() -> None:
    specs = specs_from_states([S.FULLY_GELLED, None, S.NOT_GELLED], 2)
    frame = render_row(specs, SMALL)
    assert frame.tube_labels is not None
    present = set(np.unique(frame.tube_labels)) - {0}
    assert present == {1, 3}


def test_masks_align_with_image() -> None:
    specs = specs_from_states([S.FULLY_GELLED] * 8, 4)
    before, after = render_pair(specs, SMALL, seed=3)
    gray_before = before.image.mean(axis=2)
    gray_after = after.image.mean(axis=2)
    for position in range(1, 9):
        tube = before.tube_mask(position) > 0
        gel = before.gel_mask(position) > 0
        assert gel.any() and not (gel & ~tube).any()
        # 凝胶比同管内的空管部分暗，管内比背景暗或相当
        empty = tube & ~gel
        assert gray_before[gel].mean() < 0.7 * gray_before[empty].mean()
        # 翻转后管占据的区域基本相同（只有圆头和平口互换），凝胶移到管的另一端
        flipped = after.tube_mask(position) > 0
        assert np.count_nonzero(tube & flipped) / np.count_nonzero(tube | flipped) > 0.97
        geometry = slot_geometry(SMALL, position)
        ys_before = np.nonzero(gel)[0].mean()
        ys_after = np.nonzero(after.gel_mask(position) > 0)[0].mean()
        assert ys_before > geometry.center_y > ys_after
        assert gray_after[after.gel_mask(position) > 0].mean() < gray_after[empty].mean()


def test_gel_area_ordering_across_states() -> None:
    ratios: dict[GelState, list[float]] = {state: [] for state in GelState}
    for seed in range(6):
        states = [S.FULLY_GELLED, S.PARTIALLY_GELLED, S.NOT_GELLED] * 2 + [S.FULLY_GELLED] * 2
        before, after = render_pair(specs_from_states(states, seed), SMALL, seed=seed)
        for position, state in enumerate(states, start=1):
            area_before = np.count_nonzero(before.gel_mask(position))
            area_after = np.count_nonzero(after.gel_mask(position, largest_only=True))
            ratios[state].append(area_after / area_before)
    assert min(ratios[S.FULLY_GELLED]) > 0.9
    assert max(ratios[S.PARTIALLY_GELLED]) < 0.8
    assert min(ratios[S.PARTIALLY_GELLED]) > 0.3
    assert max(ratios[S.NOT_GELLED]) < 0.35
    assert max(ratios[S.PARTIALLY_GELLED]) > max(ratios[S.NOT_GELLED])


def test_partially_gelled_has_detached_blob() -> None:
    specs = specs_from_states([S.PARTIALLY_GELLED], 1)
    _, after = render_pair(specs, SMALL)
    whole = after.gel_mask(1)
    main = after.gel_mask(1, largest_only=True)
    assert np.count_nonzero(whole) > np.count_nonzero(main)
    assert np.array_equal(largest_component(whole), main)


def test_zero_pool_leaves_no_visible_gel() -> None:
    spec = TubeSpec(state=S.NOT_GELLED, pool_fraction=0.0)
    before, after = render_pair([spec], SMALL)
    assert np.count_nonzero(before.gel_mask(1)) > 0
    assert np.count_nonzero(after.gel_mask(1)) == 0


def test_flip_animation_frames_are_intermediate() -> None:
    specs = specs_from_states([S.FULLY_GELLED], 1)
    start = render_row(specs, SMALL, flip_phase=0.0)
    middle = render_row(specs, SMALL, flip_phase=0.5)
    end = render_row(specs, SMALL, flip_phase=1.0, settle=1.0)
    assert start.tube_labels is not None and middle.tube_labels is not None
    # 翻到一半时试管在轴向被压缩
    assert np.count_nonzero(middle.tube_labels) < 0.2 * np.count_nonzero(start.tube_labels)
    assert end.tube_labels is not None
    assert np.count_nonzero(end.tube_labels) == np.count_nonzero(start.tube_labels)
