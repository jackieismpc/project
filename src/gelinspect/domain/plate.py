"""12 排×8 支孔板的管位模型。

管号 1–96 按排编号：第 1 排是 1–8，对应 Row01；第 2 排是 9–16，对应 Row02；依此类推，
第 12 排是 89–96；每排内从左到右。
这里的“排”（Row）是机械臂一次抓取、同时翻转和拍照的 8 支试管，
在孔板上表现为竖直的一列。“孔”是一排内的位置，从 1 开始。

所有编号都从 1 开始，与界面和报告一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from gelinspect.domain.errors import InvalidPlatePositionError

ROW_COUNT = 12
"""孔板的排数。"""
HOLES_PER_ROW = 8
"""每排的孔数。"""
TUBE_COUNT = ROW_COUNT * HOLES_PER_ROW
"""孔板的总管位数。"""


@dataclass(frozen=True, slots=True)
class PlatePosition:
    """孔板上的一个位置。

    Attributes:
        row_index: 排号，1–12。
        hole_index: 孔号，即排内位置，1–8。
    """

    row_index: int
    hole_index: int

    def __post_init__(self) -> None:
        """校验排号和孔号的范围。

        Raises:
            InvalidPlatePositionError: 排号或孔号超出范围。
        """
        _require_row(self.row_index)
        if not 1 <= self.hole_index <= HOLES_PER_ROW:
            raise InvalidPlatePositionError(f"孔号应在 1–{HOLES_PER_ROW} 内：{self.hole_index}")

    @property
    def tube_no(self) -> int:
        """该位置的管号。"""
        return (self.row_index - 1) * HOLES_PER_ROW + self.hole_index


def _require_row(row_index: int) -> None:
    """校验排号的范围。

    Args:
        row_index: 排号。

    Raises:
        InvalidPlatePositionError: 排号超出范围。
    """
    if not 1 <= row_index <= ROW_COUNT:
        raise InvalidPlatePositionError(f"排号应在 1–{ROW_COUNT} 内：{row_index}")


def tube_to_position(tube_no: int) -> PlatePosition:
    """把管号换算成排和孔。

    Args:
        tube_no: 管号，1–96。

    Returns:
        对应的孔板位置。

    Raises:
        InvalidPlatePositionError: 管号超出范围。
    """
    if not 1 <= tube_no <= TUBE_COUNT:
        raise InvalidPlatePositionError(f"管号应在 1–{TUBE_COUNT} 内：{tube_no}")
    row_offset, hole_offset = divmod(tube_no - 1, HOLES_PER_ROW)
    return PlatePosition(row_index=row_offset + 1, hole_index=hole_offset + 1)


def position_to_tube(row_index: int, hole_index: int) -> int:
    """把排和孔换算成管号。

    Args:
        row_index: 排号，1–12。
        hole_index: 孔号，1–8。

    Returns:
        管号，1–96。

    Raises:
        InvalidPlatePositionError: 排号或孔号超出范围。
    """
    return PlatePosition(row_index=row_index, hole_index=hole_index).tube_no


def tubes_in_row(row_index: int) -> tuple[int, ...]:
    """列出一排的全部管号。

    Args:
        row_index: 排号，1–12。

    Returns:
        按孔号升序排列的 8 个管号。

    Raises:
        InvalidPlatePositionError: 排号超出范围。
    """
    _require_row(row_index)
    first = (row_index - 1) * HOLES_PER_ROW + 1
    return tuple(range(first, first + HOLES_PER_ROW))


def row_label(row_index: int) -> str:
    """生成排的显示标签。

    Args:
        row_index: 排号，1–12。

    Returns:
        形如 ``Row01`` 的标签。

    Raises:
        InvalidPlatePositionError: 排号超出范围。
    """
    _require_row(row_index)
    return f"Row{row_index:02d}"
