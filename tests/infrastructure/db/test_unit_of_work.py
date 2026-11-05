"""工作单元：状态、结果、媒体和审计同一事务提交；崩溃注入；并发下的哈希链。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.interfaces.storage import (
    AuditQuery,
    DuplicateError,
    MediaFile,
    MediaKind,
    NotFoundError,
    PageRequest,
    StateConflictError,
    StorageError,
    TemperatureSample,
    TransactionalStorage,
    UnitOfWork,
)

from .conftest import T0, make_event, make_experiment, make_result

SHA = "a" * 64


def media(file_id: str = "F1", experiment_id: str = "E1") -> MediaFile:
    return MediaFile(
        file_id=file_id,
        experiment_id=experiment_id,
        kind=MediaKind.IMAGE,
        path=f"/x/{file_id}.png",
        sha256=SHA,
        created_at=T0,
        row_index=1,
    )


def write_all(uow: UnitOfWork) -> None:
    uow.experiments.update_state("E1", ExperimentState.ANALYZING)
    uow.experiments.save_results("E1", [make_result(1)])
    uow.media.add(media())
    uow.audit.append(make_event(1, experiment_id="E1"))


def row_counts(db: Database) -> tuple[ExperimentState, int, int, int]:
    experiment = db.experiments.get("E1")
    assert experiment is not None
    audit = db.audit.query(AuditQuery(experiment_id="E1"), PageRequest()).total
    return experiment.state, len(experiment.results), len(db.media.list_for_experiment("E1")), audit


@pytest.fixture
def seeded(database: Database) -> Database:
    database.experiments.save(make_experiment(with_results=False))
    return database


def test_database_satisfies_transactional_storage(seeded: Database) -> None:
    storage: TransactionalStorage = seeded
    with storage.unit_of_work() as uow:
        write_all(uow)
    assert row_counts(seeded) == (ExperimentState.ANALYZING, 1, 1, 1)
    assert verify_audit_chain(seeded).ok


def test_exception_rolls_back_everything(seeded: Database) -> None:
    with pytest.raises(RuntimeError, match="注入"), seeded.unit_of_work() as uow:
        write_all(uow)
        raise RuntimeError("注入")
    assert row_counts(seeded) == (ExperimentState.DRAFT, 0, 0, 0)
    assert verify_audit_chain(seeded).ok
    # 回滚后链头没有前进：下一条接在创世哈希之后，链仍然完好。
    assert seeded.audit.append(make_event(9)).seq == 1


@pytest.mark.parametrize("failing_step", ["state", "results", "media", "audit"])
def test_failure_at_each_step_leaves_nothing(seeded: Database, failing_step: str) -> None:
    seeded.media.add(media("DUP"))
    before = row_counts(seeded)

    def step(name: str) -> None:
        if name == failing_step:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError), seeded.unit_of_work() as uow:
        step("state")
        uow.experiments.update_state("E1", ExperimentState.ANALYZING)
        step("results")
        uow.experiments.save_results("E1", [make_result(1)])
        step("media")
        uow.media.add(media())
        step("audit")
        uow.audit.append(make_event(1, experiment_id="E1"))
    assert row_counts(seeded) == before
    assert verify_audit_chain(seeded).ok


def test_commit_failure_rolls_back_and_raises_storage_error(seeded: Database) -> None:
    from sqlalchemy import event

    def fail(_connection: object) -> None:
        raise sqlite3.OperationalError("注入：磁盘已满")

    event.listen(seeded.engine, "commit", fail)
    try:
        with pytest.raises(StorageError), seeded.unit_of_work() as uow:
            write_all(uow)
    finally:
        event.remove(seeded.engine, "commit", fail)
    assert row_counts(seeded) == (ExperimentState.DRAFT, 0, 0, 0)
    assert verify_audit_chain(seeded).ok


def test_swallowed_repository_error_cannot_commit_partial_writes(seeded: Database) -> None:
    seeded.media.add(media("DUP"))
    with pytest.raises(StorageError), seeded.unit_of_work() as uow:
        uow.audit.append(make_event(1, experiment_id="E1"))
        with pytest.raises(DuplicateError):
            uow.media.add(media("DUP"))
    assert seeded.audit.query(AuditQuery(), PageRequest()).total == 0


def test_integrity_errors_are_translated_inside_the_unit_of_work(seeded: Database) -> None:
    with pytest.raises(NotFoundError), seeded.unit_of_work() as uow:
        uow.media.add(media("X", experiment_id="不存在"))


def test_repositories_refuse_use_after_the_unit_of_work(seeded: Database) -> None:
    with seeded.unit_of_work() as uow:
        audit = uow.audit
    with pytest.raises(StorageError, match="已结束"):
        audit.append(make_event())
    assert verify_audit_chain(seeded).ok


def test_several_appends_in_one_unit_of_work_chain_correctly(seeded: Database) -> None:
    """同一会话里多次追加：链头不能读到身份映射里的旧值。"""
    with seeded.unit_of_work() as uow:
        first = uow.audit.append(make_event(1))
        uow.audit.append_many([make_event(2), make_event(3)])
        last = uow.audit.append(make_event(4))
    assert (first.seq, last.seq) == (1, 4)
    assert verify_audit_chain(seeded).ok
    assert seeded.audit.append(make_event(5)).seq == 5
    assert verify_audit_chain(seeded).ok


def test_results_can_be_overwritten_twice_in_one_unit_of_work(seeded: Database) -> None:
    with seeded.unit_of_work() as uow:
        uow.experiments.save_results("E1", [make_result(1)])
        uow.experiments.save_results("E1", [make_result(1, state=None)])
    experiment = seeded.experiments.get("E1")
    assert experiment is not None and experiment.results[1].gel_state is None


def test_update_state_compares_expected_state(seeded: Database) -> None:
    seeded.experiments.update_state("E1", ExperimentState.READY, expected=ExperimentState.DRAFT)
    with pytest.raises(StateConflictError):
        seeded.experiments.update_state(
            "E1", ExperimentState.INCUBATING, expected=ExperimentState.DRAFT
        )
    with pytest.raises(NotFoundError):
        seeded.experiments.update_state("无", ExperimentState.READY)
    experiment = seeded.experiments.get("E1")
    assert experiment is not None
    assert experiment.state is ExperimentState.READY
    # 只改状态：管位不受影响。
    assert len(experiment.assignments) == 4


def test_update_state_leaves_results_untouched(seeded: Database) -> None:
    seeded.experiments.save_results("E1", [make_result(1)])
    seeded.experiments.update_state("E1", ExperimentState.ANALYZING)
    experiment = seeded.experiments.get("E1")
    assert experiment is not None and set(experiment.results) == {1}


def test_unit_of_work_writes_telemetry_atomically(seeded: Database) -> None:
    sample = TemperatureSample(T0, "incubator", 37.0, "E1")
    with pytest.raises(RuntimeError), seeded.unit_of_work() as uow:
        uow.telemetry.add_temperatures([sample])
        raise RuntimeError
    page = seeded.telemetry.query_temperatures(experiment_id="E1", page=PageRequest())
    assert page.total == 0


def test_write_lock_is_taken_on_entry(seeded: Database, tmp_path: Path) -> None:
    """进入工作单元就持有写锁，事务内先读后写不会基于过期快照。"""
    outsider = sqlite3.connect(tmp_path / "gel.db", timeout=0.05)
    try:
        with seeded.unit_of_work():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                outsider.execute("UPDATE audit_chain_head SET last_seq = last_seq")
            outsider.rollback()
        outsider.execute("UPDATE audit_chain_head SET last_seq = last_seq")
        outsider.commit()
    finally:
        outsider.close()


def test_lock_timeout_surfaces_as_storage_error(seeded: Database, tmp_path: Path) -> None:
    from sqlalchemy import event

    def short_timeout(connection: sqlite3.Connection, _record: object) -> None:
        connection.execute("PRAGMA busy_timeout=20")

    event.listen(seeded.engine, "connect", short_timeout)
    seeded.engine.dispose()
    holder = sqlite3.connect(tmp_path / "gel.db")
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StorageError), seeded.unit_of_work():
            pass
    finally:
        holder.rollback()
        holder.close()
    assert verify_audit_chain(seeded).ok


def test_concurrent_units_of_work_and_plain_appends_keep_the_chain(seeded: Database) -> None:
    """工作单元、回滚和普通追加并发：链不分叉，提交的条数一分不差。"""
    threads_n, rounds = 6, 25
    committed = [0] * threads_n
    errors: list[BaseException] = []
    barrier = threading.Barrier(threads_n)

    def worker(index: int) -> None:
        try:
            barrier.wait()
            for round_no in range(rounds):
                kind = (index + round_no) % 3
                if kind == 0:
                    with seeded.unit_of_work() as uow:
                        uow.audit.append(make_event(round_no, experiment_id="E1"))
                        uow.audit.append(make_event(round_no, experiment_id="E1"))
                        uow.media.add(media(uuid.uuid4().hex))
                    committed[index] += 2
                elif kind == 1:
                    with pytest.raises(RuntimeError), seeded.unit_of_work() as uow:
                        uow.audit.append(make_event(round_no, experiment_id="E1"))
                        raise RuntimeError
                else:
                    seeded.audit.append(make_event(round_no, experiment_id="E1"))
                    committed[index] += 1
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60.0)
    assert errors == []
    verification = verify_audit_chain(seeded)
    assert verification.ok, verification.message
    assert verification.checked == sum(committed)


# ---------------------------------------------------------------------- 真实进程崩溃

_CRASH_SCRIPT = dedent(
    """
    import os, sys
    from gelinspect.domain.state_machine import ExperimentState
    from gelinspect.infrastructure.db import open_database
    from tests.infrastructure.db.conftest import make_event, make_experiment, make_result
    from tests.infrastructure.db.test_unit_of_work import media

    db = open_database(sys.argv[1])
    point = sys.argv[2]
    db.experiments.save(make_experiment(with_results=False))
    db.audit.append(make_event(0, experiment_id="E1"))

    def maybe_crash(name):
        if name == point:
            os._exit(42)

    with db.unit_of_work() as uow:
        maybe_crash("begin")
        uow.experiments.update_state("E1", ExperimentState.ANALYZING)
        maybe_crash("after_state")
        uow.experiments.save_results("E1", [make_result(1)])
        maybe_crash("after_results")
        uow.media.add(media())
        maybe_crash("after_media")
        uow.audit.append(make_event(1, experiment_id="E1"))
        maybe_crash("after_audit")
    maybe_crash("after_commit")
    os._exit(0)
    """
)


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ("begin", (ExperimentState.DRAFT, 0, 0, 1)),
        ("after_state", (ExperimentState.DRAFT, 0, 0, 1)),
        ("after_results", (ExperimentState.DRAFT, 0, 0, 1)),
        ("after_media", (ExperimentState.DRAFT, 0, 0, 1)),
        ("after_audit", (ExperimentState.DRAFT, 0, 0, 1)),
        ("after_commit", (ExperimentState.ANALYZING, 1, 1, 2)),
    ],
)
def test_process_crash_commits_all_or_nothing(
    tmp_path: Path, point: str, expected: tuple[ExperimentState, int, int, int]
) -> None:
    """进程在事务中途被杀（``os._exit`` 不做任何清理），重开后要么全有要么全无。"""
    path = tmp_path / "crash.db"
    script = tmp_path / "crash_script.py"
    script.write_text(_CRASH_SCRIPT, encoding="utf-8")
    root = Path(__file__).parents[3]
    env = {**os.environ, "PYTHONPATH": str(root)}
    done = subprocess.run(
        [sys.executable, str(script), str(path), point],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 42, done.stderr

    database = open_database(path)
    try:
        assert row_counts(database) == expected
        assert verify_audit_chain(database).ok
        # 崩溃之后链还能继续接。
        database.audit.append(make_event(7, action=AuditAction.LOGIN))
        assert verify_audit_chain(database).ok
    finally:
        database.close()
