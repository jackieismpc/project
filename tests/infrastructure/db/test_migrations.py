"""迁移、PRAGMA 与外键。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from gelinspect.domain.enums import ExperimentType
from gelinspect.infrastructure.db import Database, open_database
from gelinspect.infrastructure.db.database import MIGRATIONS_DIR
from gelinspect.infrastructure.db.models import Base
from gelinspect.interfaces.storage import GENESIS_HASH, DatabaseVersionError

from .conftest import make_event


def test_open_creates_parent_dirs_and_latest_schema(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "gel.db"
    db = open_database(path)
    try:
        assert path.exists()
        with db.session() as session:
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            tables = {
                row[0]
                for row in session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
        assert version == "0006"
        assert set(Base.metadata.tables) <= tables
    finally:
        db.close()


def test_reopen_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "gel.db"
    first = open_database(path)
    first.audit.append(make_event())
    first.close()
    second = open_database(path)
    try:
        assert second.audit.verify_chain().checked == 1
    finally:
        second.close()


def test_wal_and_foreign_keys_enabled(database: Database) -> None:
    with database.session() as session:
        assert session.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_foreign_keys_enforced(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO reports (report_id, experiment_id, path, sha256, created_by,"
                " created_at, signed) VALUES ('r', 'missing', 'p', 'h', 'u',"
                " '2026-01-01T00:00:00.000000Z', 0)"
            )
        )


def test_migration_schema_matches_orm_models(database: Database) -> None:
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": False})
        diff = compare_metadata(context, Base.metadata)
    assert diff == []


def test_chain_head_initialised_by_migration(database: Database) -> None:
    with database.session() as session:
        row = session.execute(text("SELECT id, last_seq, last_hash FROM audit_chain_head")).one()
    assert tuple(row) == (1, 0, GENESIS_HASH)


def test_does_not_depend_on_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert not (elsewhere / "alembic.ini").exists()
    db = open_database(tmp_path / "gel.db")
    db.close()
    assert (MIGRATIONS_DIR / "env.py").is_file()
    assert (MIGRATIONS_DIR / "versions" / "rev_0001_initial_schema.py").is_file()


def test_database_from_newer_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gel.db"
    open_database(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999'")
    with pytest.raises(DatabaseVersionError):
        open_database(path)


def test_indexes_exist(database: Database) -> None:
    with database.session() as session:
        names = {
            row[0]
            for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        }
    expected = {
        "ix_audit_events_occurred_at",
        "ix_audit_events_actor_seq",
        "ix_audit_events_action_seq",
        "ix_audit_events_experiment_id_seq",
        "ix_experiments_created_at",
        "ix_experiments_experiment_type_created_at",
        "ix_experiments_state_created_at",
        "ix_reagent_batches_batch_no",
        "ix_tube_assignments_sample_batch_no",
        "ix_tube_results_review_status",
        "ix_media_files_experiment_id_row_index",
        "ix_temperature_log_recorded_at",
        "ix_device_log_recorded_at",
    }
    assert expected <= names


def _alembic(path: Path, action: str, revision: str) -> None:
    """直接跑 Alembic，和 ``database._migrate`` 用同一个迁移目录。"""
    engine = create_engine(f"sqlite:///{path}")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, action)(config, revision)
    engine.dispose()


def _upgrade_from_0002(path: Path) -> Database:
    """建一个 0002 版本的库，放入旧数据，再用当前程序打开（升级到最新）。"""
    _alembic(path, "upgrade", "0002")
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            INSERT INTO templates (template_id, name, experiment_type, needs_confirmation, builtin)
              VALUES ('builtin.sensitivity_confirmation_2lambda_1', '灵敏度',
                      'sensitivity_confirmation', 0, 1),
                     ('custom.a', '自定义', 'sensitivity_confirmation', 0, 0),
                     ('builtin.limit', '限度', 'limit', 1, 1);
            INSERT INTO template_items (template_id, tube_no, sample_name, role, spiked_endotoxin)
              VALUES ('builtin.sensitivity_confirmation_2lambda_1', 1, 'x', 'test', 1.0),
                     ('builtin.sensitivity_confirmation_2lambda_1', 5, 'x', 'test', 0.5),
                     ('builtin.sensitivity_confirmation_2lambda_1', 9, 'x', 'test', 0.25),
                     ('builtin.sensitivity_confirmation_2lambda_1', 13, 'x', 'test', 0.125),
                     ('builtin.sensitivity_confirmation_2lambda_1', 17, 'x',
                      'negative_control', 0.0),
                     ('custom.a', 1, 'y', 'test', 0.3);
            INSERT INTO experiments (experiment_id, experiment_type, template_id, instrument_no,
                                     operator, created_at, state)
              VALUES ('DONE', 'sensitivity_confirmation', 'custom.a', 'G', 'op',
                      '2026-01-01T00:00:00.000000Z', 'completed'),
                     ('RUN', 'limit', 'builtin.limit', 'G', 'op',
                      '2026-01-02T00:00:00.000000Z', 'incubating'),
                     ('GONE', 'limit', 'deleted', 'G', 'op',
                      '2026-01-03T00:00:00.000000Z', 'stopped');
            INSERT INTO audit_events (seq, occurred_at, actor, action, detail, experiment_id,
                                      prev_hash, hash)
              VALUES (1, '2026-01-01T01:00:00.000000Z', 'system', 'experiment_state_changed',
                      'analyzing → pending_review（x）', 'DONE', 'a', 'b'),
                     (2, '2026-01-01T02:00:00.000000Z', 'alice', 'experiment_state_changed',
                      'pending_review → completed（review_completed）：全部', 'DONE', 'b', 'c');
            """
        )
    return open_database(path)


def test_upgrade_converts_builtin_template_and_keeps_custom(tmp_path: Path) -> None:
    db = _upgrade_from_0002(tmp_path / "old.db")
    try:
        builtin = db.templates.get("builtin.sensitivity_confirmation_2lambda_1")
        assert builtin is not None
        by_tube = {i.tube_no: i for i in builtin.items}
        assert [by_tube[n].lambda_multiple for n in (1, 5, 9, 13, 17)] == [2.0, 1.0, 0.5, 0.25, 0.0]
        assert all(i.spiked_endotoxin is None for i in builtin.items)
        custom = db.templates.get("custom.a")
        assert custom is not None
        assert custom.items[0].spiked_endotoxin == 0.3
        assert custom.items[0].lambda_multiple is None
    finally:
        db.close()


def test_upgrade_backfills_completed_at_and_layout_confirmed(tmp_path: Path) -> None:
    db = _upgrade_from_0002(tmp_path / "old.db")
    try:
        done = db.experiments.get("DONE")
        running = db.experiments.get("RUN")
        gone = db.experiments.get("GONE")
        assert done is not None and running is not None and gone is not None
        assert done.completed_at == datetime(2026, 1, 1, 2, tzinfo=UTC)
        assert running.completed_at is None
        assert done.layout_confirmed is True
        assert running.layout_confirmed is False  # 推定模板
        assert gone.layout_confirmed is False  # 模板已删除，按未确认
        assert done.conclusion is None  # 不补算旧结论
    finally:
        db.close()


def test_downgrade_restores_absolute_values(tmp_path: Path) -> None:
    path = tmp_path / "gel.db"
    open_database(path).close()
    _alembic(path, "downgrade", "0002")
    with sqlite3.connect(path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert "experiment_conclusions" not in tables
        columns = {r[1] for r in connection.execute("PRAGMA table_info(experiments)")}
        assert "completed_at" not in columns


def test_upgrade_folds_retest_into_limit(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _alembic(path, "upgrade", "0005")
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            INSERT INTO templates (template_id, name, experiment_type, needs_confirmation, builtin)
              VALUES ('builtin.retest', '复试试验', 'retest', 1, 1),
                     ('custom.r', '我的复试', 'retest', 0, 0);
            INSERT INTO template_items (template_id, tube_no, sample_name, role)
              VALUES ('builtin.retest', 1, '复试试验', 'unconfirmed'),
                     ('custom.r', 1, '样品', 'sample');
            INSERT INTO experiments (experiment_id, experiment_type, template_id, instrument_no,
                                     operator, created_at, state)
              VALUES ('R1', 'retest', 'builtin.retest', 'G', 'op',
                      '2026-01-01T00:00:00.000000Z', 'stopped');
            """
        )
    db = open_database(path)
    try:
        assert db.templates.get("builtin.retest") is None
        custom = db.templates.get("custom.r")
        assert custom is not None and custom.experiment_type is ExperimentType.LIMIT
        experiment = db.experiments.get("R1")
        assert experiment is not None and experiment.experiment_type is ExperimentType.LIMIT
    finally:
        db.close()
