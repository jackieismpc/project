"""仓储共用的类型与分页辅助。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, TypeVar

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from gelinspect.interfaces.storage import PageRequest, StorageBusyError

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


_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6
_PRIMARY_CODE_MASK = 0xFF


def is_lock_error(error: BaseException) -> bool:
    """判断 SQLite 错误是否是拿不到锁。

    Args:
        error: SQLAlchemy 包装的操作错误，或底层 sqlite3 错误。

    Returns:
        主错误码是 ``SQLITE_BUSY`` 或 ``SQLITE_LOCKED`` 时为 True。
    """
    origin = error.orig if isinstance(error, OperationalError) else error
    code = getattr(origin, "sqlite_errorcode", None)
    return isinstance(code, int) and code & _PRIMARY_CODE_MASK in (_SQLITE_BUSY, _SQLITE_LOCKED)


@contextmanager
def translate_lock_errors() -> Iterator[None]:
    """把等写锁超时的 SQLite 错误转成 ``StorageBusyError``。

    用在单个仓储方法的外层（包住 ``with self._sessions()``，这样提交时的锁错误也能捕获）。
    什么都没有写入，调用方可以稍后重试。其他数据库错误原样抛出。

    Yields:
        无。

    Raises:
        StorageBusyError: 等待写锁超时。
    """
    try:
        yield
    except OperationalError as error:
        if is_lock_error(error):
            raise StorageBusyError(f"数据库正忙，等待写锁超时：{error}") from error
        raise
