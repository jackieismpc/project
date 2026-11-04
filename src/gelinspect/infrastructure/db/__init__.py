"""数据层：SQLAlchemy 2.1 + SQLite，Alembic 迁移，仓储与哈希链审计日志。

对外只暴露 :func:`open_database`、:class:`Database`、:func:`seed_defaults`
和 :func:`verify_audit_chain`。ORM 模型（``models``）不属于公共接口，
调用方通过 ``gelinspect.interfaces.storage`` 的仓储接口与 domain 实体交互。
"""

from gelinspect.infrastructure.db.database import Database, open_database
from gelinspect.infrastructure.db.seed import SeedResult, seed_defaults
from gelinspect.interfaces.storage import ChainVerification


def verify_audit_chain(database: Database) -> ChainVerification:
    """校验审计哈希链，定位第一条被篡改或删除的记录。

    Args:
        database: 已打开的数据库。

    Returns:
        校验结果；``first_bad_seq`` 是第一处异常的序号。
    """
    return database.audit.verify_chain()


__all__ = ["Database", "SeedResult", "open_database", "seed_defaults", "verify_audit_chain"]
