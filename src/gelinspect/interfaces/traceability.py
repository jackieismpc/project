"""溯源接口：试剂码解析、备案校验和政府溯源占位。

试剂码格式、备案清单和政府平台接口目前都未提供（``docs/PLAN.md`` 第 7 节第 6 项），
因此三者都是可替换的接口。备案校验的结果使用领域枚举，由试验状态机决定是否锁定。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from pydantic import Field

from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind
from gelinspect.interfaces._base import ContractModel


class TraceabilityError(Exception):
    """溯源异常基类。"""


class ReagentCodeFormatError(TraceabilityError):
    """试剂码格式无法解析。"""


class ReagentRegistryUnavailableError(TraceabilityError):
    """备案清单不可用。调用方应锁定试验，不得视为校验通过。"""


class TraceabilitySubmissionError(TraceabilityError):
    """向政府溯源平台提交失败，调用方可以稍后重试。"""


class ReagentCode(ContractModel):
    """解析后的试剂码。

    Attributes:
        raw: 扫码得到的原始字符串。
        product_code: 产品编码。
        batch_no: 批号。
        kind: 试剂种类，码中没有时为 None。
        expiry_date: 有效期，码中没有时为 None。
        serial_no: 序列号，码中没有时为 None。
    """

    raw: str = Field(min_length=1)
    product_code: str = Field(min_length=1)
    batch_no: str = Field(min_length=1)
    kind: ReagentKind | None = None
    expiry_date: date | None = None
    serial_no: str | None = None


class ReagentVerification(ContractModel):
    """一次备案校验的结果。

    Attributes:
        code: 被校验的试剂码。
        status: 校验结果，不是 ``VALID`` 时试验会被锁定。
        message: 面向用户的补充说明。
    """

    code: ReagentCode
    status: ReagentCheckStatus
    message: str | None = None


class TraceabilityRecord(ContractModel):
    """上报给政府溯源平台的记录。字段在平台规范明确后再细化。

    Attributes:
        experiment_id: 试验标识。
        reagent_codes: 试验使用的试剂码原文。
        payload: 平台要求的其他字段。
    """

    experiment_id: str = Field(min_length=1)
    reagent_codes: list[str] = Field(default_factory=list)
    payload: dict[str, str] = Field(default_factory=dict)


class TraceabilityReceipt(ContractModel):
    """政府溯源平台的回执。

    Attributes:
        receipt_id: 回执编号。
    """

    receipt_id: str = Field(min_length=1)


@runtime_checkable
class ReagentCodeParser(Protocol):
    """试剂码解析器。"""

    def parse(self, raw: str) -> ReagentCode:
        """解析扫码得到的字符串。

        Args:
            raw: 原始字符串。

        Returns:
            解析结果。

        Raises:
            ReagentCodeFormatError: 格式无法解析。
        """
        ...


@runtime_checkable
class ReagentRegistry(Protocol):
    """试剂备案校验。"""

    def verify(self, code: ReagentCode, on_date: date) -> ReagentVerification:
        """校验试剂是否备案且在有效期内。

        Args:
            code: 解析后的试剂码。
            on_date: 校验日期。

        Returns:
            校验结果。未备案或过期是正常结果，不抛异常。

        Raises:
            ReagentRegistryUnavailableError: 备案清单不可用。
        """
        ...


@runtime_checkable
class GovernmentTraceabilityClient(Protocol):
    """政府溯源平台占位接口。软件离线运行，本次不提供真实实现。"""

    def is_enabled(self) -> bool:
        """查询是否启用了政府溯源。

        Returns:
            已启用时返回 True。未启用时业务层跳过上报。
        """
        ...

    def submit(self, record: TraceabilityRecord) -> TraceabilityReceipt:
        """提交一条溯源记录。

        Args:
            record: 溯源记录。

        Returns:
            平台回执。

        Raises:
            TraceabilitySubmissionError: 提交失败。
        """
        ...
