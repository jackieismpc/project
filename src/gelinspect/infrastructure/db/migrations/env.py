"""Alembic 运行环境。

连接由 :func:`gelinspect.infrastructure.db.database.open_database` 通过
``config.attributes["connection"]`` 传入，因此既不需要 ``alembic.ini``，
也不依赖当前工作目录，打包后可以直接运行。
"""

from __future__ import annotations

from alembic import context
from sqlalchemy.engine import Connection

from gelinspect.infrastructure.db.models import Base

config = context.config


def run_migrations() -> None:
    """用调用方传入的连接执行迁移。

    Raises:
        RuntimeError: 调用方没有传入连接。
    """
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("迁移需要通过 config.attributes['connection'] 传入连接")
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
