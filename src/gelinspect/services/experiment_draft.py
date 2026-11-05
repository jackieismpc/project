"""新试验向导的草稿：模板、样品行、管位选择和试剂码。

纯数据和规则，不依赖 Qt：界面只负责显示和收集输入，「数量是否对得上」「选中的管号如何对应
样品行」都在这里判断，测试不需要界面也能覆盖。

管号一律用 ``domain.plate``（竖向编号，Row01 是 1–8）。选中的管号升序后依次对应样品行，
与模板里按管号升序的管位项一一对应。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gelinspect.domain.enums import ReagentKind, TubeRole
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.templates import ExperimentTemplate


@dataclass(slots=True)
class SampleRow:
    """样品表的一行（对应模板里的一个管位项）。

    Attributes:
        name: 样品名称。
        batch_no: 样品批号，可留空。
        spiked_endotoxin: 外加内毒素。按倍数的行由 λ 算出，λ 未知时为 None。
        dilution_factor: 稀释倍数。
        role: 试管用途（来自模板，界面不编辑）。
        lambda_multiple: 模板给的 λ 倍数；有值时这一行的外加内毒素不能手工改。
    """

    name: str
    batch_no: str = ""
    spiked_endotoxin: float | None = None
    dilution_factor: float | None = None
    role: TubeRole = TubeRole.TEST
    lambda_multiple: float | None = None

    def snapshot(self) -> tuple[str, str, float | None, float | None]:
        """用于判断是否被编辑过的快照（按 λ 算出的值不算编辑）。

        Returns:
            可比较的元组。
        """
        spike = None if self.lambda_multiple is not None else self.spiked_endotoxin
        return (self.name, self.batch_no, spike, self.dilution_factor)


@dataclass(frozen=True, slots=True)
class SampleAssignmentInput:
    """创建试验时一支试管的输入。

    Attributes:
        tube_no: 管号，1–80。
        name: 样品名称。
        batch_no: 样品批号。
        spiked_endotoxin: 外加内毒素。
        dilution_factor: 稀释倍数。
    """

    tube_no: int
    name: str
    batch_no: str = ""
    spiked_endotoxin: float | None = None
    dilution_factor: float | None = None


@dataclass(frozen=True, slots=True)
class NewExperimentRequest:
    """创建试验的请求。服务层只信任这里的原始输入，自己查模板、重新校验试剂。

    Attributes:
        template_id: 模板标识。
        assignments: 每支试管的管号和样品；按管号升序对应模板里按管号升序的管位项。
        reagent_codes: 三种试剂的扫码原文。
        mvd: 最大有效稀释倍数，可选；写入试验记录并显示在报告里。
        endotoxin_limit: 内毒素限值（EU/mL），可选。
    """

    template_id: str
    assignments: Sequence[SampleAssignmentInput]
    reagent_codes: Mapping[ReagentKind, str] = field(default_factory=dict)
    mvd: float | None = None
    endotoxin_limit: float | None = None


def parse_limit(text: str) -> float | None:
    """解析 MVD 或内毒素限值的输入。

    Args:
        text: 输入的文字。

    Returns:
        大于 0 的有限数；留空、不是数字、不大于 0 或不是有限数时为 None。
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


class ExperimentDraft:
    """向导的草稿状态。"""

    def __init__(self) -> None:
        """创建空草稿。"""
        self._template: ExperimentTemplate | None = None
        self._rows: list[SampleRow] = []
        self._baseline: list[tuple[str, str, float | None, float | None]] = []
        self._selected: list[int] = []
        self._reagent_codes: dict[ReagentKind, str] = {}
        self._labeled_sensitivity: float | None = None
        self.mvd_text = ""
        self.limit_text = ""

    # ------------------------------------------------------------------ MVD 与限值

    @property
    def mvd(self) -> float | None:
        """录入的 MVD；留空或不合法时为 None。"""
        return parse_limit(self.mvd_text)

    @property
    def endotoxin_limit(self) -> float | None:
        """录入的内毒素限值；留空或不合法时为 None。"""
        return parse_limit(self.limit_text)

    def limit_problems(self) -> list[str]:
        """已录入但不合法（不是大于 0 的数）的项。

        Returns:
            ``mvd`` 和 ``endotoxin_limit`` 中有问题的字段名。
        """
        problems: list[str] = []
        if self.mvd_text.strip() and self.mvd is None:
            problems.append("mvd")
        if self.limit_text.strip() and self.endotoxin_limit is None:
            problems.append("endotoxin_limit")
        return problems

    # ------------------------------------------------------------------ 模板

    @property
    def template(self) -> ExperimentTemplate | None:
        """当前选中的模板。"""
        return self._template

    def choose_template(self, template: ExperimentTemplate) -> None:
        """选择模板：样品行取模板默认值，已选管位清空。

        Args:
            template: 模板。
        """
        self._template = template
        self._rows = [
            SampleRow(
                name=item.sample_name,
                spiked_endotoxin=item.spike_for(self._labeled_sensitivity),
                dilution_factor=item.dilution_factor,
                role=item.role,
                lambda_multiple=item.lambda_multiple,
            )
            for item in sorted(template.items, key=lambda i: i.tube_no)
        ]
        self._baseline = [row.snapshot() for row in self._rows]
        self._selected = []

    @property
    def rows(self) -> list[SampleRow]:
        """样品行（可直接修改行内容）。"""
        return self._rows

    # ------------------------------------------------------------------ 管位

    @property
    def expected_count(self) -> int:
        """模板要求的试管数。"""
        return len(self._rows)

    @property
    def selected(self) -> list[int]:
        """已选管号，升序。"""
        return list(self._selected)

    @property
    def selected_count(self) -> int:
        """已选管数。"""
        return len(self._selected)

    @property
    def difference(self) -> int:
        """已选数量减应选数量：负数是还差几个，正数是多选了几个，0 表示对得上。"""
        return self.selected_count - self.expected_count

    @property
    def tubes_match(self) -> bool:
        """已选数量是否与模板一致（且至少有一支）。"""
        return self.expected_count > 0 and self.difference == 0

    def set_selected(self, tubes: Iterable[int]) -> None:
        """设置已选管位。

        Args:
            tubes: 管号。

        Raises:
            InvalidPlatePositionError: 管号超出 1–80。
        """
        unique = sorted(set(tubes))
        for tube_no in unique:
            tube_to_position(tube_no)
        self._selected = unique

    def auto_select(self) -> list[int]:
        """自动选择：选中模板默认的管号。

        Returns:
            选中的管号。
        """
        if self._template is None:
            self._selected = []
        else:
            self._selected = sorted(item.tube_no for item in self._template.items)
        return self.selected

    def clear_selection(self) -> None:
        """清除全部已选管位。"""
        self._selected = []

    def tube_of_row(self, index: int) -> int | None:
        """第 ``index`` 行（从 0 起）对应的管号。

        Args:
            index: 行序号。

        Returns:
            管号；选中的管数不够时为 None。
        """
        return self._selected[index] if 0 <= index < len(self._selected) else None

    # ------------------------------------------------------------------ 试剂

    @property
    def reagent_codes(self) -> dict[ReagentKind, str]:
        """三种试剂的扫码原文（已通过校验的才会留在这里）。"""
        return self._reagent_codes

    @property
    def labeled_sensitivity(self) -> float | None:
        """当前鲎试剂的标示灵敏度 λ。"""
        return self._labeled_sensitivity

    def set_labeled_sensitivity(self, value: float | None) -> None:
        """记录鲎试剂的 λ，并重新计算按倍数的外加内毒素。

        Args:
            value: 标示灵敏度，没有时为 None。
        """
        self._labeled_sensitivity = value
        if self._template is None:
            return
        items = sorted(self._template.items, key=lambda i: i.tube_no)
        for row, item in zip(self._rows, items, strict=True):
            if item.lambda_multiple is not None:
                row.spiked_endotoxin = item.spike_for(value)

    @property
    def lambda_missing(self) -> bool:
        """当前模板需要 λ，但鲎试剂没有提供。"""
        return (
            self._template is not None
            and self._template.requires_lambda
            and (self._labeled_sensitivity is None or self._labeled_sensitivity <= 0)
        )

    # ------------------------------------------------------------------ 状态

    def has_sample_input(self) -> bool:
        """是否已有用户录入的样品内容或已选管位（更换模板会清空它们）。

        Returns:
            选过管位或编辑过样品行时为 True。
        """
        return bool(self._selected) or [row.snapshot() for row in self._rows] != self._baseline

    def is_modified(self) -> bool:
        """向导里是否已有用户录入的内容（离开时需要确认）。

        Returns:
            录入过试剂、选过管位，或编辑过样品行时为 True。
        """
        return (
            bool(self._reagent_codes)
            or self.has_sample_input()
            or bool(self.mvd_text.strip() or self.limit_text.strip())
        )

    def sample_problems(self) -> list[int]:
        """样品名称为空的行号（从 1 起）。

        Returns:
            行号列表。
        """
        return [i for i, row in enumerate(self._rows, start=1) if not row.name.strip()]

    def to_request(self) -> NewExperimentRequest:
        """生成创建试验的请求。

        Returns:
            请求；管号不足的行不会出现，服务层会据此拒绝数量不符的请求。

        Raises:
            ValueError: 还没有选择模板。
        """
        if self._template is None:
            raise ValueError("还没有选择模板")
        assignments = [
            SampleAssignmentInput(
                tube_no=tube_no,
                name=row.name.strip(),
                batch_no=row.batch_no.strip(),
                spiked_endotoxin=row.spiked_endotoxin,
                dilution_factor=row.dilution_factor,
            )
            for row, tube_no in zip(self._rows, self._selected, strict=False)
        ]
        return NewExperimentRequest(
            template_id=self._template.template_id,
            assignments=assignments,
            reagent_codes=dict(self._reagent_codes),
            mvd=self.mvd,
            endotoxin_limit=self.endotoxin_limit,
        )
