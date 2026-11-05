"""迁移、PRAGMA 与外键。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
        assert version == "0002"
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
