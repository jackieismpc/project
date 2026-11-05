"""模拟试剂白名单：试剂码解析、备案校验和政府溯源占位。

试剂码格式（模拟约定，真实格式待甲方提供）::

    产品编码|批号|种类|有效期|序列号

``种类`` 是 :class:`~gelinspect.domain.enums.ReagentKind` 的值，``有效期`` 为 ``YYYYMMDD``
或留空，``序列号`` 可省略。白名单中含有效、已过期和未备案三类样例，见 :data:`SAMPLE_CODES`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind
from gelinspect.interfaces.traceability import (
    ReagentCode,
    ReagentCodeFormatError,
    ReagentRegistryUnavailableError,
    ReagentVerification,
    TraceabilityReceipt,
    TraceabilityRecord,
    TraceabilitySubmissionError,
)


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """白名单中的一条备案批次。

    Attributes:
        product_code: 产品编码。
        batch_no: 批号。
        kind: 试剂种类。
        expiry_date: 备案有效期。
        labeled_sensitivity: 标示灵敏度，仅鲎试剂有。
    """

    product_code: str
    batch_no: str
    kind: ReagentKind
    expiry_date: date
    labeled_sensitivity: float | None = None


WHITELIST: tuple[RegistryEntry, ...] = (
    RegistryEntry("GL-LAL-0.25", "LAL2606A", ReagentKind.LAL_REAGENT, date(2027, 6, 30), 0.25),
    RegistryEntry("GL-LAL-0.25", "LAL2501B", ReagentKind.LAL_REAGENT, date(2026, 1, 31), 0.25),
    RegistryEntry("GL-CSE-10", "CSE2605A", ReagentKind.ENDOTOXIN, date(2027, 5, 31)),
    RegistryEntry("GL-BET-W", "WAT2607A", ReagentKind.WATER, date(2028, 7, 31)),
)
"""模拟的备案清单。``LAL2501B`` 已过期。"""

SAMPLE_CODES: dict[str, str] = {
    "lal_valid": "GL-LAL-0.25|LAL2606A|lal_reagent|20270630|0001",
    "lal_expired": "GL-LAL-0.25|LAL2501B|lal_reagent|20260131|0002",
    "lal_unregistered": "GL-LAL-0.25|LAL9999X|lal_reagent|20270630|0003",
    "endotoxin_valid": "GL-CSE-10|CSE2605A|endotoxin|20270531|0001",
    "water_valid": "GL-BET-W|WAT2607A|water||0001",
}
"""演示用的扫码内容。"""


class SimReagentCodeParser:
    """模拟试剂码解析器。"""

    def parse(self, raw: str) -> ReagentCode:
        """解析 ``产品编码|批号|种类|有效期|序列号``。

        Args:
            raw: 扫码得到的字符串。

        Returns:
            解析结果。

        Raises:
            ReagentCodeFormatError: 字段数量、种类或日期不合法。
        """
        parts = raw.strip().split("|")
        if not 3 <= len(parts) <= 5 or not parts[0] or not parts[1]:
            raise ReagentCodeFormatError(f"无法解析试剂码：{raw!r}")
        try:
            kind = ReagentKind(parts[2])
            expiry = (
                datetime.strptime(parts[3], "%Y%m%d").date()
                if len(parts) > 3 and parts[3]
                else None
            )
        except ValueError as exc:
            raise ReagentCodeFormatError(f"试剂码的种类或有效期不合法：{raw!r}") from exc
        serial = parts[4] if len(parts) > 4 and parts[4] else None
        return ReagentCode(
            raw=raw,
            product_code=parts[0],
            batch_no=parts[1],
            kind=kind,
            expiry_date=expiry,
            serial_no=serial,
        )

    def sample_codes(self) -> Mapping[str, str]:
        """示例扫码内容，界面在试剂校验处列出，点一下填入输入框。

        Returns:
            显示名称（批号加结果）到扫码原文。
        """
        return {
            "LAL2606A (valid)": SAMPLE_CODES["lal_valid"],
            "LAL2501B (expired)": SAMPLE_CODES["lal_expired"],
            "LAL9999X (unregistered)": SAMPLE_CODES["lal_unregistered"],
            "CSE2605A (valid)": SAMPLE_CODES["endotoxin_valid"],
            "WAT2607A (valid)": SAMPLE_CODES["water_valid"],
        }


class SimReagentRegistry:
    """模拟备案校验，数据来自 :data:`WHITELIST`。

    Attributes:
        available: 为 False 时 ``verify`` 抛 ``ReagentRegistryUnavailableError``。
    """

    def __init__(
        self, entries: tuple[RegistryEntry, ...] = WHITELIST, *, available: bool = True
    ) -> None:
        """创建备案清单。

        Args:
            entries: 备案条目。
            available: 清单是否可用，用于注入故障。
        """
        self._entries = {(e.product_code, e.batch_no): e for e in entries}
        self.available = available

    def verify(self, code: ReagentCode, on_date: date) -> ReagentVerification:
        """校验备案和有效期。码中的有效期优先，没有时用备案有效期。

        Args:
            code: 解析后的试剂码。
            on_date: 校验日期。

        Returns:
            校验结果。

        Raises:
            ReagentRegistryUnavailableError: 清单不可用。
        """
        if not self.available:
            raise ReagentRegistryUnavailableError("模拟的备案清单不可用")
        entry = self._entries.get((code.product_code, code.batch_no))
        if entry is None:
            return ReagentVerification(
                code=code, status=ReagentCheckStatus.NOT_REGISTERED, message="未在备案清单中"
            )
        expiry = code.expiry_date or entry.expiry_date
        if on_date > expiry:
            return ReagentVerification(
                code=code,
                status=ReagentCheckStatus.EXPIRED,
                message=f"已于 {expiry} 过期",
                expiry_date=expiry,
                labeled_sensitivity=entry.labeled_sensitivity,
            )
        return ReagentVerification(
            code=code,
            status=ReagentCheckStatus.VALID,
            expiry_date=expiry,
            labeled_sensitivity=entry.labeled_sensitivity,
        )


class SimTraceabilityClient:
    """模拟政府溯源平台：默认未启用，启用后在内存中保存记录。

    Attributes:
        enabled: 是否启用。
        fail_next: 为真时下一次 ``submit`` 失败，用于测试重试。
        records: 已提交的记录。
    """

    def __init__(self, *, enabled: bool = False) -> None:
        """创建客户端。

        Args:
            enabled: 是否启用。
        """
        self.enabled = enabled
        self.fail_next = False
        self.records: list[TraceabilityRecord] = []

    def is_enabled(self) -> bool:
        """查询是否启用。

        Returns:
            已启用时为 True。
        """
        return self.enabled

    def submit(self, record: TraceabilityRecord) -> TraceabilityReceipt:
        """提交一条记录。

        Args:
            record: 溯源记录。

        Returns:
            回执，编号按提交顺序递增。

        Raises:
            TraceabilitySubmissionError: 注入了失败。
        """
        if self.fail_next:
            self.fail_next = False
            raise TraceabilitySubmissionError("模拟的提交失败")
        self.records.append(record)
        return TraceabilityReceipt(receipt_id=f"SIM-{len(self.records):06d}")
