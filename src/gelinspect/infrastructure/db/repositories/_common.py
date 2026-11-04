"""仓储共用的类型与分页辅助。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, TypeVar

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from gelinspect.interfaces.storage import PageRequest

SessionProvider = Callable[[], AbstractContextManager[Session]]
"""返回事务会话上下文的可调用对象，通常是 ``Database.session``。"""

T = TypeVar("T")


def count_where(session: Session, entity: Any, filters: list[ColumnElement[bool]]) -> int:
    """统计满足条件的行数。

    Args:
        session: 会话。
        entity: ORM 类。
        filters: 过滤条件，全部满足才计入。

    Returns:
        行数。
    """
    statement = select(func.count()).select_from(entity).where(*filters)
    return int(session.execute(statement).scalar_one())


def page_of(statement: Select[Any], page: PageRequest) -> Select[Any]:
    """给查询加上偏移和条数限制。

    Args:
        statement: 已排序的查询。
        page: 分页请求。

    Returns:
        带分页的查询。
    """
    return statement.offset(page.offset).limit(page.limit)
