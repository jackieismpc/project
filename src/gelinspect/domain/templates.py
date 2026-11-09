"""试验模板和内置模板（四类试验，限度试验另有复试模板）。

灵敏度复核模板取自参考视频；其余模板只有管数可考，布局为推定，
以 ``needs_confirmation=True`` 显式标记，用途和数值留空，等待甲方 QA 核对。

灵敏度模板存**标示灵敏度 λ 的倍数**（2、1、0.5、0.25，阴性对照 0），创建试验时才按鲎试剂的 λ
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


def _sensitivity_template() -> ExperimentTemplate:
    """构造灵敏度复核（2λ:1）模板。

    Returns:
        17 管模板：4 个浓度梯度各 4 管，加 1 支阴性对照。
    """
    # 参考视频新试验页（49.5 秒）中“外加内毒素”列是 1、0.5、0.25、0.125，那次试验 λ=0.5，
    # 即 2λ、λ、λ/2、λ/4。这里存倍数，λ 不是 0.5 时才算得对。
    name = "灵敏度复核"
    items = [
        TemplateTubeItem(
            tube_no=index * 4 + offset,
            sample_name=name,
            role=TubeRole.TEST,
            lambda_multiple=multiple,
        )
        for index, multiple in enumerate((2.0, 1.0, 0.5, 0.25))
        for offset in range(1, 5)
    ]
    items.append(
        TemplateTubeItem(
            tube_no=17,
            sample_name="阴性对照",
            role=TubeRole.NEGATIVE_CONTROL,
            lambda_multiple=0.0,
        )
    )
    return ExperimentTemplate(
        template_id="builtin.sensitivity_confirmation_2lambda_1",
        name="灵敏度复核 (2λ:1)",
        experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
        items=tuple(items),
    )


def _presumed_template(
    template_id: str, name: str, experiment_type: ExperimentType, tube_count: int
) -> ExperimentTemplate:
    """构造只有管数可考的推定模板。

    推定内容只有“从 1 号管起连续占位”。用途标记为未确认，数值留空，不做任何推断。

    Args:
        template_id: 模板标识。
        name: 显示名称。
        experiment_type: 试验类型。
        tube_count: 管数。

    Returns:
        ``needs_confirmation=True`` 的模板。
    """
    items = tuple(
        TemplateTubeItem(tube_no=tube_no, sample_name=name, role=TubeRole.UNCONFIRMED)
        for tube_no in range(1, tube_count + 1)
    )
    return ExperimentTemplate(
        template_id=template_id,
        name=name,
        experiment_type=experiment_type,
        items=items,
        needs_confirmation=True,
    )


BUILTIN_TEMPLATES: Mapping[ExperimentType, ExperimentTemplate] = MappingProxyType(
    {
        ExperimentType.SENSITIVITY_CONFIRMATION: _sensitivity_template(),
        ExperimentType.INTERFERENCE: _presumed_template(
            "builtin.interference", "干扰试验", ExperimentType.INTERFERENCE, 28
        ),
        ExperimentType.SEMI_QUANTITATIVE: _presumed_template(
            "builtin.semi_quantitative", "半定量试验", ExperimentType.SEMI_QUANTITATIVE, 16
        ),
        ExperimentType.LIMIT: _presumed_template(
            "builtin.limit", "限度试验", ExperimentType.LIMIT, 8
        ),
    }
)
"""每类试验的主模板，按试验类型索引。"""

LIMIT_RETEST_TEMPLATE = _presumed_template(
    "builtin.limit_retest", "限度试验（复试）", ExperimentType.LIMIT, 10
)
"""限度试验的复试模板：供试品两管一阳一阴时，按中国药典通则 1143 复试（供试品 4 管）。

复试属于限度试验，不是单独的试验类型。管数按参考设备推定，布局待甲方确认。
"""

ALL_BUILTIN_TEMPLATES: tuple[ExperimentTemplate, ...] = (
    *BUILTIN_TEMPLATES.values(),
    LIMIT_RETEST_TEMPLATE,
)
"""全部内置模板，写入数据库时使用。"""
