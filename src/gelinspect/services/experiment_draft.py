"""新试验向导的草稿：模板、样品行、管位选择和试剂码。

纯数据和规则，不依赖 Qt：界面只负责显示和收集输入，“数量是否对得上”“选中的管号如何对应
样品行”都在这里判断，测试不需要界面也能覆盖。

管号一律用 ``domain.plate``（竖向编号，Row01 是 1–8）。选中的管号升序后依次对应样品行，
与模板里按管号升序的管位项一一对应。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gelinspect.domain.enums import ReagentKind, TubeRole
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.templates import ExperimentTemplate, TemplateTubeItem, spike_from_lambda
from gelinspect.interfaces.traceability import ReagentInput


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
    tube_no: int | None = None
    row_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sequence_no: int = 0
    lambda_multiple_overridden: bool = False

    def snapshot(self) -> tuple[str, str, float | None, float | None, float | None]:
        """用于判断是否被编辑过的快照（按 λ 算出的值不算编辑）。

        Returns:
            可比较的元组。
        """
        return (
            self.name,
            self.batch_no,
            self.spiked_endotoxin if self.lambda_multiple is None else None,
            self.dilution_factor,
            self.lambda_multiple,
        )


@dataclass(frozen=True, slots=True)
class SampleAssignmentInput:
    """创建试验时一支试管的输入。

    Attributes:
        tube_no: 管号，1–96。
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
    lambda_multiple: float | None = None
    lambda_multiple_overridden: bool = False


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
    reagent_inputs: Mapping[ReagentKind, ReagentInput] = field(default_factory=dict)
    mvd: float | None = None
    endotoxin_limit: float | None = None
    template_snapshot: ExperimentTemplate | None = None
    source_template_id: str | None = None


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
        self._source_template_id: str | None = None
        self._rows: list[SampleRow] = []
        self._baseline: dict[str, tuple[str, str, float | None, float | None, float | None]] = {}
        self._initial_row_ids: frozenset[str] = frozenset()
        self._initial_selected: tuple[int, ...] = ()
        self._initial_tubes_by_row_id: dict[str, int | None] = {}
        self._next_sequence_no = 1
        self._selected: list[int] = []
        self._reagent_codes: dict[ReagentKind, str] = {}
        self._reagent_inputs: dict[ReagentKind, ReagentInput] = {}
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

    @property
    def source_template_id(self) -> str | None:
        """单次配置最初来源的模板标识。"""
        return self._source_template_id

    def choose_template(self, template: ExperimentTemplate) -> None:
        """选择模板：样品行取模板默认值，已选管位清空。

        Args:
            template: 模板。
        """
        self._template = template
        self._source_template_id = (
            template.template_id if not template.template_id.startswith("draft.") else None
        )
        self._rows = [
            SampleRow(
                name=item.sample_name,
                spiked_endotoxin=item.spike_for(self._labeled_sensitivity),
                dilution_factor=item.dilution_factor,
                role=item.role,
                lambda_multiple=item.lambda_multiple,
                sequence_no=index,
            )
            for index, item in enumerate(sorted(template.items, key=lambda i: i.tube_no), start=1)
        ]
        ordered_items = sorted(template.items, key=lambda i: i.tube_no)
        for row, item in zip(self._rows, ordered_items, strict=True):
            row.tube_no = item.tube_no
        self._baseline = {row.row_id: row.snapshot() for row in self._rows}
        self._selected = sorted(item.tube_no for item in template.items)
        self._initial_row_ids = frozenset(row.row_id for row in self._rows)
        self._initial_selected = tuple(self._selected)
        self._initial_tubes_by_row_id = {row.row_id: row.tube_no for row in self._rows}
        self._next_sequence_no = len(self._rows) + 1

    def configure(
        self, template: ExperimentTemplate, *, source_template_id: str | None = None
    ) -> None:
        """替换本次配置，同时保留试剂校验和限值输入。"""
        self.choose_template(template)
        self._source_template_id = source_template_id

    @property
    def rows(self) -> list[SampleRow]:
        """样品行（可直接修改行内容）。"""
        return self._rows

    def add_row(
        self,
        *,
        name: str = "",
        role: TubeRole = TubeRole.TEST,
        tube_no: int | None = None,
    ) -> SampleRow:
        """追加样品行并分配不可复用的稳定序号。"""
        if len(self._rows) >= 96:
            raise ValueError("最多支持96支试管")
        if tube_no is not None:
            tube_to_position(tube_no)
            if any(row.tube_no == tube_no for row in self._rows):
                raise ValueError(f"管位已分配：{tube_no}")
        sequence_no = self._next_sequence_no
        self._next_sequence_no += 1
        row = SampleRow(name=name, role=role, tube_no=tube_no, sequence_no=sequence_no)
        self._rows.append(row)
        self._baseline[row.row_id] = row.snapshot()
        if tube_no is not None:
            self._selected = sorted({*self._selected, tube_no})
        return row

    def remove_row(self, row_id: str) -> SampleRow:
        """按稳定行标识删除样品行及其管位绑定。"""
        index = next((i for i, row in enumerate(self._rows) if row.row_id == row_id), None)
        if index is None:
            raise KeyError(f"样品行不存在：{row_id}")
        row = self._rows.pop(index)
        self._baseline.pop(row.row_id, None)
        if row.tube_no is not None:
            self._selected = [tube for tube in self._selected if tube != row.tube_no]
        return row

    def assign_tube(self, row_id: str, tube_no: int | None) -> None:
        """为样品行绑定或解绑管位；已绑定到其他行的管位会拒绝覆盖。"""
        row = next((item for item in self._rows if item.row_id == row_id), None)
        if row is None:
            raise KeyError(f"样品行不存在：{row_id}")
        if tube_no is not None:
            tube_to_position(tube_no)
            if any(item.row_id != row_id and item.tube_no == tube_no for item in self._rows):
                raise ValueError(f"管位已分配：{tube_no}")
        previous = row.tube_no
        row.tube_no = tube_no
        self._selected = sorted({item.tube_no for item in self._rows if item.tube_no is not None})
        if previous is not None and previous not in self._selected:
            self._selected = [tube for tube in self._selected if tube != previous]

    def add_tube(
        self, tube_no: int, *, name: str = "", role: TubeRole = TubeRole.TEST
    ) -> SampleRow:
        """新增一个样品行并直接绑定指定管位。"""
        tube_to_position(tube_no)
        if any(row.tube_no == tube_no for row in self._rows):
            raise ValueError(f"管位已分配：{tube_no}")
        row = next(
            (
                item
                for item in sorted(self._rows, key=lambda item: item.sequence_no)
                if item.tube_no is None
            ),
            None,
        )
        if row is None:
            row = self.add_row(name=name, role=role)
        elif name:
            row.name = name
        if row is not None and role is not TubeRole.TEST:
            row.role = role
        self.assign_tube(row.row_id, tube_no)
        return row

    def remove_tube(self, tube_no: int) -> SampleRow | None:
        """删除指定管位对应的样品行；其他样品、批号和稳定序号不变。"""
        row = next((item for item in self._rows if item.tube_no == tube_no), None)
        if row is not None:
            return self.remove_row(row.row_id)
        self._selected = [tube for tube in self._selected if tube != tube_no]
        return None

    def row_has_user_edits(self, row_id: str) -> bool:
        """名称或批号是否相对该稳定行的初始值被用户修改。"""
        row = next((item for item in self._rows if item.row_id == row_id), None)
        if row is None:
            raise KeyError(f"样品行不存在：{row_id}")
        baseline = self._baseline.get(row_id)
        return baseline is not None and (row.name, row.batch_no) != baseline[:2]

    def sort_rows(self, key: str, *, reverse: bool = False) -> None:
        """按指定字段排序；整行移动以保留样品、参数和管位绑定。"""
        if key not in {"name", "batch_no", "tube_no", "lambda_multiple", "sequence", "sequence_no"}:
            raise ValueError(f"不支持的样品排序字段：{key}")
        indexed = list(enumerate(self._rows))
        if key in {"sequence", "sequence_no"}:
            indexed.sort(key=lambda pair: pair[1].sequence_no, reverse=reverse)
        elif key == "tube_no":
            indexed.sort(
                key=lambda pair: (pair[1].tube_no is None, pair[1].tube_no or 0), reverse=reverse
            )
        elif key == "lambda_multiple":
            indexed.sort(key=lambda pair: pair[1].lambda_multiple or 0, reverse=reverse)
        else:
            indexed.sort(
                key=lambda pair: (getattr(pair[1], key) is None, getattr(pair[1], key) or ""),
                reverse=reverse,
            )
        self._rows = [row for _, row in indexed]

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
            InvalidPlatePositionError: 管号超出 1–96。
        """
        unique = sorted(set(tubes))
        for tube_no in unique:
            tube_to_position(tube_no)
        if set(unique) == set(self._selected):
            return
        self._selected = unique
        ordered_rows = sorted(self._rows, key=lambda row: row.sequence_no)
        if len(unique) == len(ordered_rows):
            for row, tube_no in zip(ordered_rows, unique, strict=True):
                row.tube_no = tube_no
        else:
            for row in ordered_rows:
                row.tube_no = None

    def auto_select(self) -> list[int]:
        """自动选择：选中模板默认的管号。

        Returns:
            选中的管号。
        """
        if self._template is None:
            self._selected = []
        else:
            if frozenset(row.row_id for row in self._rows) == self._initial_row_ids:
                self._selected = sorted(item.tube_no for item in self._template.items)
            else:
                self._selected = list(range(1, len(self._rows) + 1))
            for row, tube_no in zip(
                sorted(self._rows, key=lambda item: item.sequence_no), self._selected, strict=True
            ):
                row.tube_no = tube_no
        return self.selected

    def clear_selection(self) -> None:
        """清除全部已选管位。"""
        self._selected = []
        for row in self._rows:
            row.tube_no = None

    def tube_of_row(self, index: int) -> int | None:
        """第 ``index`` 行（从 0 起）对应的管号。

        Args:
            index: 行序号。

        Returns:
            管号；选中的管数不够时为 None。
        """
        return self._rows[index].tube_no if 0 <= index < len(self._rows) else None

    # ------------------------------------------------------------------ 试剂

    @property
    def reagent_codes(self) -> dict[ReagentKind, str]:
        """三种试剂的扫码原文（已通过校验的才会留在这里）。"""
        return self._reagent_codes

    @property
    def reagent_inputs(self) -> dict[ReagentKind, ReagentInput]:
        """最近一次校验成功的结构化试剂输入。"""
        return self._reagent_inputs

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
        for row in self._rows:
            if row.lambda_multiple is not None:
                row.spiked_endotoxin = (
                    spike_from_lambda(value, row.lambda_multiple)
                    if value is not None and value > 0
                    else None
                )

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
        return (
            frozenset(row.row_id for row in self._rows) != self._initial_row_ids
            or tuple(self._selected) != self._initial_selected
            or {row.row_id: row.snapshot() for row in self._rows} != self._baseline
        )

    @property
    def is_customized(self) -> bool:
        """本次试管行结构或单管配置已偏离所选模板。"""
        if self._template is None:
            return False
        if frozenset(row.row_id for row in self._rows) != self._initial_row_ids:
            return True
        template_items = sorted(self._template.items, key=lambda item: item.tube_no)
        rows = sorted(self._rows, key=lambda row: row.sequence_no)
        if len(rows) != len(template_items) or any(
            row.role is not item.role
            or row.dilution_factor != item.dilution_factor
            or row.lambda_multiple != item.lambda_multiple
            for row, item in zip(rows, template_items, strict=False)
        ):
            return True
        current_mapping = {row.row_id: row.tube_no for row in rows}
        return current_mapping != self._initial_tubes_by_row_id

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
                lambda_multiple=row.lambda_multiple,
                lambda_multiple_overridden=row.lambda_multiple_overridden,
            )
            for row in self._rows
            if (tube_no := row.tube_no) is not None
        ]
        assignments.sort(key=lambda assignment: assignment.tube_no)
        template = self._template
        template_id = template.template_id
        if self.is_customized:
            template_id = f"draft.{uuid.uuid4().hex}"
            assigned_rows = sorted(
                (row for row in self._rows if row.tube_no is not None),
                key=lambda row: row.tube_no or 0,
            )
            template = ExperimentTemplate(
                template_id=template_id,
                name=self._template.name,
                experiment_type=self._template.experiment_type,
                items=tuple(
                    TemplateTubeItem(
                        tube_no=index,
                        sample_name=row.name.strip(),
                        role=row.role,
                        dilution_factor=row.dilution_factor,
                        spiked_endotoxin=(
                            None if row.lambda_multiple is not None else row.spiked_endotoxin
                        ),
                        lambda_multiple=row.lambda_multiple,
                    )
                    for index, row in enumerate(assigned_rows, start=1)
                ),
                needs_confirmation=self._template.needs_confirmation,
                created_by=self._template.created_by,
                created_at=self._template.created_at,
            )
        return NewExperimentRequest(
            template_id=template_id,
            assignments=assignments,
            reagent_codes=dict(self._reagent_codes),
            reagent_inputs=dict(self._reagent_inputs),
            mvd=self.mvd,
            endotoxin_limit=self.endotoxin_limit,
            template_snapshot=template,
            source_template_id=self._source_template_id,
        )
