import pytest

from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.plate import (
    HOLES_PER_ROW,
    ROW_COUNT,
    TUBE_COUNT,
    PlatePosition,
    position_to_tube,
    row_label,
    tube_to_position,
    tubes_in_row,
)


def test_plate_dimensions() -> None:
    assert (ROW_COUNT, HOLES_PER_ROW, TUBE_COUNT) == (12, 8, 96)


@pytest.mark.parametrize(
    ("tube_no", "row", "hole"),
    [
        (1, 1, 1),
        (8, 1, 8),
        (9, 2, 1),
        (16, 2, 8),
        (17, 3, 1),
        (73, 10, 1),
        (80, 10, 8),
        (89, 12, 1),
        (96, 12, 8),
    ],
)
def test_vertical_numbering(tube_no: int, row: int, hole: int) -> None:
    assert tube_to_position(tube_no) == PlatePosition(row, hole)
    assert position_to_tube(row, hole) == tube_no


def test_mapping_is_a_bijection() -> None:
    positions = [tube_to_position(no) for no in range(1, TUBE_COUNT + 1)]
    assert len(set(positions)) == TUBE_COUNT
    assert [p.tube_no for p in positions] == list(range(1, TUBE_COUNT + 1))


def test_tubes_in_row() -> None:
    assert tubes_in_row(1) == (1, 2, 3, 4, 5, 6, 7, 8)
    assert tubes_in_row(2) == (9, 10, 11, 12, 13, 14, 15, 16)
    assert tubes_in_row(10) == (73, 74, 75, 76, 77, 78, 79, 80)
    assert tubes_in_row(12) == (89, 90, 91, 92, 93, 94, 95, 96)


def test_row_label() -> None:
    assert row_label(1) == "Row01"
    assert row_label(10) == "Row10"
    assert row_label(12) == "Row12"


@pytest.mark.parametrize("tube_no", [0, -1, 97])
def test_invalid_tube_no(tube_no: int) -> None:
    with pytest.raises(InvalidPlatePositionError):
        tube_to_position(tube_no)


@pytest.mark.parametrize(("row", "hole"), [(0, 1), (13, 1), (1, 0), (1, 9)])
def test_invalid_position(row: int, hole: int) -> None:
    with pytest.raises(InvalidPlatePositionError):
        position_to_tube(row, hole)


@pytest.mark.parametrize("row", [0, 13])
def test_invalid_row(row: int) -> None:
    with pytest.raises(InvalidPlatePositionError):
        tubes_in_row(row)
    with pytest.raises(InvalidPlatePositionError):
        row_label(row)
