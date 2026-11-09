"""试验模板和内置模板（四类试验，限度试验另有复试模板）。

内置模板的管位布局按中国药典通则 1143 凝胶法的表 1–3 和灵敏度复核的要求排列，每管标明用途。
``needs_confirmation`` 字段保留给旧数据，内置模板都为 False。

模板存**标示灵敏度 λ 的倍数**（2、1、0.5、0.25，阴性对照 0），创建试验时才按鲎试剂的 λ
算出外加内毒素并固化到试验记录，见 :func:`spike_from_lambda`。判定规则在 ``conclusions.py``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.plate import tube_to_position


def spike_from_lambda(labeled_sensitivity: float, multiple: float) -> float:
    """按标示灵敏度和倍数算外加内毒素。

    用十进制运算，结果不会出现 ``0.30000000000000004`` 这样的二进制浮点尾巴。

    Args:
        labeled_sensitivity: 标示灵敏度 λ，EU/mL，必须大于 0。
        multiple: λ 的倍数，不能为负。

    Returns:
        外加内毒素，EU/mL。

    Raises:
        ValueError: λ 不大于 0 或倍数为负。
    """
    if labeled_sensitivity <= 0:
        raise ValueError(f"标示灵敏度必须大于 0：{labeled_sensitivity}")
    if multiple < 0:
        raise ValueError(f"倍数不能为负：{multiple}")
    return float(Decimal(repr(float(labeled_sensitivity))) * Decimal(repr(float(multiple))))


@dataclass(frozen=True, slots=True)
class TemplateTubeItem:
    """模板中的一个管位项。

    Attributes:
        tube_no: 默认管号，1–96。
        sample_name: 默认样品名称。
        role: 试管用途。
        dilution_factor: 稀释倍数，未知时为 None。
        spiked_endotoxin: 外加内毒素的绝对值（EU/mL），未知时为 None。旧的自定义模板使用。
        lambda_multiple: 外加内毒素是 λ 的多少倍，设置后优先于 ``spiked_endotoxin``，
            创建试验时按鲎试剂的 λ 计算。
    """

    tube_no: int
    sample_name: str
    role: TubeRole
    dilution_factor: float | None = None
    spiked_endotoxin: float | None = None
    lambda_multiple: float | None = None

    def __post_init__(self) -> None:
        """校验管号和数值范围。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
            ValueError: 稀释倍数或外加内毒素为负数。
        """
        tube_to_position(self.tube_no)
        for value in (self.dilution_factor, self.spiked_endotoxin, self.lambda_multiple):
            if value is not None and value < 0:
                raise ValueError(f"数值不能为负：{value}")

    def spike_for(self, labeled_sensitivity: float | None) -> float | None:
        """这支试管在给定 λ 下的外加内毒素。

        Args:
            labeled_sensitivity: 标示灵敏度 λ，未知时为 None。

        Returns:
            有倍数时按 λ 计算（λ 未知时为 None），否则是模板里的绝对值。
        """
        if self.lambda_multiple is None:
            return self.spiked_endotoxin
        if labeled_sensitivity is None or labeled_sensitivity <= 0:
            return None
        return spike_from_lambda(labeled_sensitivity, self.lambda_multiple)


@dataclass(frozen=True, slots=True)
class ExperimentTemplate:
    """试验模板。

    Attributes:
        template_id: 稳定的模板标识。
        name: 显示名称。
        experiment_type: 试验类型。
        items: 管位项，管号不重复。
        needs_confirmation: 布局是否为推定、等待甲方确认。
    """

    template_id: str
    name: str
    experiment_type: ExperimentType
    items: tuple[TemplateTubeItem, ...]
    needs_confirmation: bool = False

    def __post_init__(self) -> None:
        """校验模板至少有一个管位且管号不重复。

        Raises:
            ValueError: 模板为空或管号重复。
        """
        tube_nos = [item.tube_no for item in self.items]
        if not tube_nos:
            raise ValueError("模板至少需要一个管位")
        if len(set(tube_nos)) != len(tube_nos):
            raise ValueError(f"模板 {self.template_id} 的管号重复")

    @property
    def tube_count(self) -> int:
        """模板的试管数。"""
        return len(self.items)

    @property
    def requires_lambda(self) -> bool:
        """创建试验时是否必须有标示灵敏度 λ：灵敏度复核，或有管位按 λ 倍数计算。"""
        return self.experiment_type is ExperimentType.SENSITIVITY_CONFIRMATION or any(
            item.lambda_multiple is not None for item in self.items
        )


SERIES_MULTIPLES = (2.0, 1.0, 0.5, 0.25)
"""内毒素系列的四个浓度：λ 的倍数，从高到低。"""
SEMI_QUANTITATIVE_DILUTIONS = (1.0, 2.0, 4.0, 8.0)
"""半定量试验供试品的默认稀释系列（相对原供试品的稀释倍数），管理员可复制模板后修改。"""

_SAMPLE = "供试品"
_NEGATIVE = "阴性对照"
_POSITIVE = "阳性对照"
_SAMPLE_POSITIVE = "供试品阳性对照"
_STANDARD = "内毒素标准系列"
_SAMPLE_SERIES = "供试品内毒素系列"


class _Layout:
    """按顺序编管号的辅助类：每加一支管号加 1。"""

    def __init__(self) -> None:
        self.items: list[TemplateTubeItem] = []

    def add(
        self,
        count: int,
        sample_name: str,
        role: TubeRole,
        *,
        lambda_multiple: float | None = None,
        dilution_factor: float | None = None,
    ) -> None:
        for _ in range(count):
            self.items.append(
                TemplateTubeItem(
                    tube_no=len(self.items) + 1,
                    sample_name=sample_name,
                    role=role,
                    dilution_factor=dilution_factor,
                    lambda_multiple=lambda_multiple,
                )
            )

    def series(self, per_level: int, sample_name: str, role: TubeRole) -> None:
        """2λ、λ、λ/2、λ/4 各 ``per_level`` 支，同一浓度内的第 k 支属于第 k 个平行系列。"""
        for multiple in SERIES_MULTIPLES:
            self.add(per_level, sample_name, role, lambda_multiple=multiple)


def _sensitivity_template() -> ExperimentTemplate:
    """灵敏度复核（中国药典通则 1143）：2λ、λ、λ/2、λ/4 各 4 管，阴性对照 2 管，共 18 管。"""
    layout = _Layout()
    layout.series(4, _STANDARD, TubeRole.TEST)
    layout.add(2, _NEGATIVE, TubeRole.NEGATIVE_CONTROL, lambda_multiple=0.0)
    return ExperimentTemplate(
        template_id="builtin.sensitivity_confirmation_2lambda_1",
        name="灵敏度复核 (2λ:1)",
        experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
        items=tuple(layout.items),
    )


def _interference_template() -> ExperimentTemplate:
    """干扰试验（药典表 1），共 28 管。

    A 供试品 2 管；B 供试品加 2λ–λ/4 各 4 管；C 检查用水加 2λ–λ/4 各 2 管；D 阴性对照 2 管。
    """
    layout = _Layout()
    layout.add(2, _SAMPLE, TubeRole.SAMPLE)
    layout.series(4, _SAMPLE_SERIES, TubeRole.SAMPLE_SERIES)
    layout.series(2, _STANDARD, TubeRole.TEST)
    layout.add(2, _NEGATIVE, TubeRole.NEGATIVE_CONTROL, lambda_multiple=0.0)
    return ExperimentTemplate(
        template_id="builtin.interference",
        name="干扰试验",
        experiment_type=ExperimentType.INTERFERENCE,
        items=tuple(layout.items),
    )


def _limit_template(template_id: str, name: str, sample_tubes: int) -> ExperimentTemplate:
    """限度试验（药典表 2）。

    A 供试品；B 供试品阳性对照（加 2λ）2 管；C 阳性对照（检查用水加 2λ）2 管；D 阴性对照 2 管。
    初试 A 为 2 管，复试为 4 管。
    """
    layout = _Layout()
    layout.add(sample_tubes, _SAMPLE, TubeRole.SAMPLE)
    layout.add(2, _SAMPLE_POSITIVE, TubeRole.SAMPLE_POSITIVE_CONTROL, lambda_multiple=2.0)
    layout.add(2, _POSITIVE, TubeRole.POSITIVE_CONTROL, lambda_multiple=2.0)
    layout.add(2, _NEGATIVE, TubeRole.NEGATIVE_CONTROL, lambda_multiple=0.0)
    return ExperimentTemplate(
        template_id=template_id,
        name=name,
        experiment_type=ExperimentType.LIMIT,
        items=tuple(layout.items),
    )


def _semi_quantitative_template() -> ExperimentTemplate:
    """半定量试验（药典表 3），共 20 管。

    A 供试品按 1、2、4、8 倍稀释各 2 管；B 供试品阳性对照 2 管；C 检查用水加 2λ–λ/4 各 2 管；
    D 阴性对照 2 管。
    """
    layout = _Layout()
    for dilution in SEMI_QUANTITATIVE_DILUTIONS:
        layout.add(2, _SAMPLE, TubeRole.SAMPLE, dilution_factor=dilution)
    layout.add(
        2,
        _SAMPLE_POSITIVE,
        TubeRole.SAMPLE_POSITIVE_CONTROL,
        lambda_multiple=2.0,
        dilution_factor=1.0,
    )
    layout.series(2, _STANDARD, TubeRole.TEST)
    layout.add(2, _NEGATIVE, TubeRole.NEGATIVE_CONTROL, lambda_multiple=0.0)
    return ExperimentTemplate(
        template_id="builtin.semi_quantitative",
        name="半定量试验",
        experiment_type=ExperimentType.SEMI_QUANTITATIVE,
        items=tuple(layout.items),
    )


BUILTIN_TEMPLATES: Mapping[ExperimentType, ExperimentTemplate] = MappingProxyType(
    {
        ExperimentType.SENSITIVITY_CONFIRMATION: _sensitivity_template(),
        ExperimentType.INTERFERENCE: _interference_template(),
        ExperimentType.SEMI_QUANTITATIVE: _semi_quantitative_template(),
        ExperimentType.LIMIT: _limit_template("builtin.limit", "限度试验", 2),
    }
)
"""每类试验的主模板，按试验类型索引。"""

LIMIT_RETEST_TEMPLATE = _limit_template("builtin.limit_retest", "限度试验（复试）", 4)
"""限度试验的复试模板：初试供试品两管一阳一阴时复试，供试品 4 管，共 10 管。"""

ALL_BUILTIN_TEMPLATES: tuple[ExperimentTemplate, ...] = (
    *BUILTIN_TEMPLATES.values(),
    LIMIT_RETEST_TEMPLATE,
)
"""全部内置模板，写入数据库时使用。"""
