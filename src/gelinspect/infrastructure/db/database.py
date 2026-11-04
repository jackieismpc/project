"""数据库的创建、升级与会话管理。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from gelinspect.infrastructure.db.repositories.audit import SqlAuditRepository
from gelinspect.infrastructure.db.repositories.experiments import SqlExperimentRepository
from gelinspect.infrastructure.db.repositories.files import SqlMediaRepository, SqlReportRepository
from gelinspect.infrastructure.db.repositories.telemetry import SqlTelemetryRepository
from gelinspect.infrastructure.db.repositories.templates import SqlTemplateRepository
from gelinspect.infrastructure.db.repositories.users import SqlUserRepository
from gelinspect.interfaces.storage import DatabaseVersionError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
"""迁移脚本目录，用包内路径定位。打包时必须把整个目录作为数据文件带上。"""

BUSY_TIMEOUT_MS = 5000
"""写锁等待时间。"""


def _configure_connection(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    """为每个新连接启用 WAL 与外键。

    Args:
        dbapi_connection: 底层 sqlite3 连接。
        _record: 连接池记录，未使用。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def _migrate(engine: Engine) -> None:
    """把数据库升级到最新版本。

    Args:
        engine: 数据库引擎。

    Raises:
        DatabaseVersionError: 数据库版本比当前程序更新，或迁移失败。
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except CommandError as error:
        raise DatabaseVersionError(f"数据库迁移失败，可能由更新版本的程序创建：{error}") from error


class Database:
    """已打开的数据库，持有引擎并提供会话和各仓储。

    Attributes:
        engine: SQLAlchemy 引擎。
    """

    def __init__(self, engine: Engine) -> None:
        """包装一个已迁移的引擎。

        Args:
            engine: 已升级到最新版本的引擎。
        """
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """打开一个事务：正常退出提交，异常回滚。

        Yields:
            会话。
        """
        with self._sessions.begin() as session:
            yield session

    def close(self) -> None:
        """关闭全部连接。"""
        self.engine.dispose()

    @property
    def users(self) -> SqlUserRepository:
        """用户仓储。"""
        return SqlUserRepository(self.session)

    @property
    def templates(self) -> SqlTemplateRepository:
        """模板仓储。"""
        return SqlTemplateRepository(self.session)

    @property
    def experiments(self) -> SqlExperimentRepository:
        """试验仓储。"""
        return SqlExperimentRepository(self.session)

    @property
    def audit(self) -> SqlAuditRepository:
        """审计仓储。"""
        return SqlAuditRepository(self.session)

    @property
    def media(self) -> SqlMediaRepository:
        """媒体文件记录仓储。"""
        return SqlMediaRepository(self.session)

    @property
    def reports(self) -> SqlReportRepository:
        """报告记录仓储。"""
        return SqlReportRepository(self.session)

    @property
    def telemetry(self) -> SqlTelemetryRepository:
        """温度与设备日志仓储。"""
        return SqlTelemetryRepository(self.session)


def open_database(path: str | Path) -> Database:
    """创建或升级数据库并打开。

    父目录不存在时自动创建。连接启用 WAL 和外键。迁移脚本用包内路径定位，
    不依赖 ``alembic.ini`` 和当前工作目录。

    Args:
        path: SQLite 文件路径。

    Returns:
        已升级到最新版本的数据库。

    Raises:
        DatabaseVersionError: 数据库版本比当前程序更新，或迁移失败。
    """
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{file}")
    event.listen(engine, "connect", _configure_connection)
    try:
        _migrate(engine)
    except Exception:
        engine.dispose()
        raise
    return Database(engine)
