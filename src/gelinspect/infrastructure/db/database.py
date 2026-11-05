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
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from gelinspect.infrastructure.db.repositories._common import is_lock_error
from gelinspect.infrastructure.db.repositories.audit import SqlAuditRepository
from gelinspect.infrastructure.db.repositories.experiments import SqlExperimentRepository
from gelinspect.infrastructure.db.repositories.files import SqlMediaRepository, SqlReportRepository
from gelinspect.infrastructure.db.repositories.reagent_checks import SqlReagentCheckRepository
from gelinspect.infrastructure.db.repositories.telemetry import SqlTelemetryRepository
from gelinspect.infrastructure.db.repositories.templates import SqlTemplateRepository
from gelinspect.infrastructure.db.repositories.users import SqlUserRepository
from gelinspect.interfaces.storage import DatabaseVersionError, StorageBusyError, StorageError

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


class SqlUnitOfWork:
    """绑定到同一会话的仓储集合，实现 ``interfaces.storage.UnitOfWork``。

    仓储通过会话提供者取会话，这里的提供者只交出同一个会话并 ``flush``，
    不提交，所以完整性错误仍在仓储方法内抛出并被转换，提交由 ``Database.unit_of_work`` 负责。
    """

    def __init__(self, session: Session) -> None:
        """绑定会话。

        Args:
            session: 已开始事务的会话。
        """
        self._session = session
        self._open = True
        self._poisoned = False
        self._experiments = SqlExperimentRepository(self._bound)
        self._audit = SqlAuditRepository(self._bound)
        self._media = SqlMediaRepository(self._bound)
        self._telemetry = SqlTelemetryRepository(self._bound)
        self._templates = SqlTemplateRepository(self._bound)
        self._reagent_checks = SqlReagentCheckRepository(self._bound)
        self._reports = SqlReportRepository(self._bound)
        self._users = SqlUserRepository(self._bound)

    @contextmanager
    def _bound(self) -> Iterator[Session]:
        """交出本事务的会话；异常穿过时记下，防止调用方吞掉后提交半截写入。

        Yields:
            本事务的会话。

        Raises:
            StorageError: 工作单元已结束。
        """
        if not self._open:
            raise StorageError("工作单元已结束，不能继续使用")
        try:
            yield self._session
            self._session.flush()
        except BaseException:
            self._poisoned = True
            raise

    @property
    def poisoned(self) -> bool:
        """是否有写入失败过。为真时事务不能提交。"""
        return self._poisoned

    def close(self) -> None:
        """结束工作单元，之后再用其中的仓储会报错。"""
        self._open = False

    @property
    def experiments(self) -> SqlExperimentRepository:
        """本事务的试验仓储。"""
        return self._experiments

    @property
    def audit(self) -> SqlAuditRepository:
        """本事务的审计仓储。"""
        return self._audit

    @property
    def media(self) -> SqlMediaRepository:
        """本事务的媒体文件记录仓储。"""
        return self._media

    @property
    def telemetry(self) -> SqlTelemetryRepository:
        """本事务的温度与设备日志仓储。"""
        return self._telemetry

    @property
    def templates(self) -> SqlTemplateRepository:
        """本事务的模板仓储。"""
        return self._templates

    @property
    def reagent_checks(self) -> SqlReagentCheckRepository:
        """本事务的试剂校验记录仓储。"""
        return self._reagent_checks

    @property
    def reports(self) -> SqlReportRepository:
        """本事务的报告记录仓储。"""
        return self._reports

    @property
    def users(self) -> SqlUserRepository:
        """本事务的用户仓储。"""
        return self._users


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

    @contextmanager
    def unit_of_work(self) -> Iterator[SqlUnitOfWork]:
        """开启工作单元：多个仓储的写入在同一事务提交，异常时全部回滚。

        事务以 ``BEGIN IMMEDIATE`` 开始，进入时就持有写锁。这样事务内先读后写（例如审计链头）
        不会读到过期的快照，也不会在升级写锁时才失败。等待写锁最多 ``busy_timeout``。

        Yields:
            绑定到本事务的仓储集合。

        Raises:
            StorageBusyError: 等待写锁超时。事务还没有开始，调用方的代码块没有执行，可以重试。
            StorageError: 提交失败，或事务内有写入失败却被调用方吞掉。
        """
        uow: SqlUnitOfWork | None = None
        try:
            with self._sessions.begin() as session:
                try:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                except OperationalError as error:
                    if is_lock_error(error):
                        raise StorageBusyError(f"等待写锁超时：{error}") from error
                    raise
                uow = SqlUnitOfWork(session)
                yield uow
                if uow.poisoned:
                    raise StorageError("工作单元内有写入失败，事务已回滚")
        except (SQLAlchemyError, sqlite3.Error) as error:
            raise StorageError(f"工作单元提交失败：{error}") from error
        finally:
            if uow is not None:
                uow.close()

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

    @property
    def reagent_checks(self) -> SqlReagentCheckRepository:
        """试剂校验记录仓储。"""
        return SqlReagentCheckRepository(self.session)


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
