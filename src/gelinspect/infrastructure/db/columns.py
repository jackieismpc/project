"""自定义列类型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
"""定长 UTC 文本格式，字典序等于时间序，也是审计哈希的规范化时间表示。"""


def format_utc(value: datetime) -> str:
    """把带时区的时间规范化为 UTC 定长文本。

    Args:
        value: 带时区的时间。

    Returns:
        形如 ``2026-01-02T03:04:05.000006Z`` 的文本。

    Raises:
        ValueError: 时间不带时区。
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带时区")
    return value.astimezone(UTC).strftime(_FORMAT)


class UtcDateTime(TypeDecorator[datetime]):
    """以 UTC 定长文本存储时间，读出时为带 UTC 时区的 ``datetime``。

    SQLite 没有时区类型，直接存储会丢失时区。拒绝不带时区的时间，保证跨平台无歧义。
    """

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        """写入前规范化为 UTC 文本。

        Args:
            value: 带时区的时间或 None。
            dialect: 数据库方言。

        Returns:
            UTC 文本或 None。
        """
        return None if value is None else format_utc(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        """读出后还原为带 UTC 时区的时间。

        Args:
            value: 数据库中的文本或 None。
            dialect: 数据库方言。

        Returns:
            带 UTC 时区的时间或 None。
        """
        if value is None:
            return None
        return datetime.strptime(value, _FORMAT).replace(tzinfo=UTC)
