"""试验模板和五类内置模板。

灵敏度复核模板取自参考视频；其余四类只有管数可考，布局为推定，
以 ``needs_confirmation=True`` 显式标记，用途和数值留空，等待甲方 QA 核对。
本模块不包含任何药典判定规则，判定规则由 P10 负责。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.plate import tube_to_position


@dataclass(frozen=True, slots=True)
class TemplateTubeItem:
    """模板中的一个管位项。

    Attributes:
        tube_no: 默认管号，1–80。
        sample_name: 默认样品名称。
        role: 试管用途。
        dilution_factor: 稀释倍数，未知时为 None。
        spiked_endotoxin: 外加内毒素，未知时为 None。
    """

    tube_no: int
    sample_name: str
    role: TubeRole
    dilution_factor: float | None = None
    spiked_endotoxin: float | None = None

    def __post_init__(self) -> None:
        """校验管号和数值范围。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
            ValueError: 稀释倍数或外加内毒素为负数。
        """
        tube_to_position(self.tube_no)
        for value in (self.dilution_factor, self.spiked_endotoxin):
            if value is not None and value < 0:
                raise ValueError(f"数值不能为负：{value}")


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


def _sensitivity_template() -> ExperimentTemplate:
    """构造灵敏度复核（2λ:1）模板。

    Returns:
        17 管模板：4 个浓度梯度各 4 管，加 1 支阴性对照。
    """
    # 参考视频新试验页（49.5 秒）中，这组值位于「外加内毒素」列，页面没有稀释倍数列。
    name = "灵敏度复核"
    items = [
        TemplateTubeItem(
            tube_no=index * 4 + offset,
            sample_name=name,
            role=TubeRole.TEST,
            spiked_endotoxin=value,
        )
        for index, value in enumerate((1.0, 0.5, 0.25, 0.125))
        for offset in range(1, 5)
    ]
    items.append(
        TemplateTubeItem(
            tube_no=17,
            sample_name="阴性对照",
            role=TubeRole.NEGATIVE_CONTROL,
            spiked_endotoxin=0.0,
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

    推定内容只有「从 1 号管起连续占位」。用途标记为未确认，数值留空，不做任何推断。

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
        ExperimentType.RETEST: _presumed_template(
            "builtin.retest", "复试试验", ExperimentType.RETEST, 10
        ),
        ExperimentType.SEMI_QUANTITATIVE: _presumed_template(
            "builtin.semi_quantitative", "半定量试验", ExperimentType.SEMI_QUANTITATIVE, 16
        ),
        ExperimentType.LIMIT: _presumed_template(
            "builtin.limit", "限度试验", ExperimentType.LIMIT, 8
        ),
    }
)
"""五类内置模板，按试验类型索引。"""
