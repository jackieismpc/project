"""并发和进程崩溃：停止与到时竞争、线程回到基线、崩溃后结果与审计一致。"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import threading
from pathlib import Path
from textwrap import dedent

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.infrastructure.db import open_database, verify_audit_chain
from gelinspect.interfaces.storage import AuditQuery, MediaKind, PageRequest
from gelinspect.services.experiment_engine import ExperimentEngine
from simulation.clock import ManualClock
from tests.services.engine_helpers import (
    EXP_ID,
    OPERATOR,
    Gate,
    fast_config,
    make_harness,
    tubes_of_rows,
)


@pytest.mark.parametrize("seed", range(12))
def test_stop_racing_with_row_due(tmp_path: Path, thread_baseline: None, seed: int) -> None:
    """停止和排到时同时发生：无论谁先，都停得干净，状态路径合法，审计链完好。"""
    h = make_harness(tmp_path, tubes_of_rows(1, 2))
    rng = random.Random(seed)
    try:
        h.save_and_start()
        h.incubating()
        barrier = threading.Barrier(2)

        def fire() -> None:
            barrier.wait()
            h.schedule.advance(h.engine.config.incubation_s + 1.0)

        def stop() -> None:
            barrier.wait()
            # 让停止落在读数流程的不同位置。
            threading.Event().wait(rng.random() * 0.15)
            h.engine.stop(OPERATOR, "竞争")

        threads = [threading.Thread(target=fire), threading.Thread(target=stop)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        h.finished()

        stored = h.stored()
        # 停止可能晚于全部读完；只有这两种结局。
        assert stored.state in (S.STOPPED, S.COMPLETED)
        h.assert_clean()
        h.assert_media_intact()
        assert h.log.states()[-1] is stored.state
        # 每次状态变化都有审计，且一条不多。
        changes = h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)
        assert len(changes) == len(h.log.states())
        # 有结果的排一定有分析审计，反过来也一样。
        analyzed = len(h.audit_details(AuditAction.ROW_ANALYZED))
        rows_with_results = {tube_to_position(no).row_index for no in stored.results}
        assert analyzed == len(rows_with_results)
        names = [name for name, _ in h.device.calls]
        assert names.count("restore_row") >= names.count("flip_row")
    finally:
        h.close()


def test_repeated_runs_return_to_the_thread_baseline(tmp_path: Path, thread_baseline: None) -> None:
    """同一个引擎连续跑多个试验，线程不增长。"""
    h = make_harness(tmp_path)
    try:
        for index in range(1, 6):
            experiment_id = f"EXP-RUN-{index}"
            h.save_and_start(experiment_id)
            h.incubating()
            h.fire_rows()
            h.finished()
            assert h.stored(experiment_id).state is S.COMPLETED
            names = {t.name for t in threading.enumerate()}
            assert not {n for n in names if n.startswith(("experiment-engine", "engine-"))}
            h.assert_clean()
        assert verify_audit_chain(h.database).ok
    finally:
        h.close()


def test_engine_keeps_an_externally_started_pipeline_running(
    tmp_path: Path, thread_baseline: None
) -> None:
    """管线不是引擎启动的（例如界面在预览），引擎结束后不能关掉它。"""
    h = make_harness(tmp_path)
    try:
        h.pipeline.start()
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        h.finished()
        assert h.stored().state is S.COMPLETED
        assert h.pipeline.is_running
        h.assert_clean()
    finally:
        h.close()


def test_audit_chain_stays_valid_with_a_concurrent_writer(
    tmp_path: Path, thread_baseline: None
) -> None:
    """引擎提交事务的同时，其他线程（例如界面的登录审计）也在追加。"""
    from tests.infrastructure.db.conftest import make_event

    h = make_harness(tmp_path, tubes_of_rows(1, 2, 3))
    stop = threading.Event()
    written = [0]

    def writer() -> None:
        index = 0
        while not stop.is_set():
            h.database.audit.append(make_event(index))
            written[0] += 1
            index += 1

    thread = threading.Thread(target=writer)
    try:
        thread.start()
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        h.finished()
        assert h.stored().state is S.COMPLETED
    finally:
        stop.set()
        thread.join(10.0)
    try:
        verification = verify_audit_chain(h.database)
        assert verification.ok, verification.message
        assert verification.checked == written[0] + len(h.audit())
    finally:
        h.close()


def test_commands_are_nonblocking_while_device_is_busy(
    tmp_path: Path, thread_baseline: None
) -> None:
    h = make_harness(tmp_path)
    gate = Gate()
    h.device.gates["flip_row"] = gate
    try:
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        gate.wait_entered()
        # 引擎线程卡在设备动作上，从其他线程发命令、读快照都立即返回。
        assert h.engine.resume(OPERATOR)
        assert h.engine.snapshot() is not None
        assert h.engine.stop(OPERATOR, "x")
        assert not h.engine.wait(0.0)
    finally:
        gate.release.set()
        h.close()


_CRASH_SCRIPT = dedent(
    """
    import os, sys
    from contextlib import contextmanager
    from pathlib import Path

    from gelinspect.domain.enums import AuditAction
    from gelinspect.services.experiment_engine import ExperimentEngine
    from tests.services.engine_helpers import (
        OPERATOR, fast_config, make_harness, tubes_of_rows,
    )

    root, point = Path(sys.argv[1]), sys.argv[2]


    class CrashingAudit:
        def __init__(self, inner, session_state):
            self._inner, self._state = inner, session_state

        def append_many(self, events):
            count = self._inner.append_many(events)
            if any(e.action is AuditAction.ROW_ANALYZED for e in events):
                self._state["row_committing"] = True
            return count


    class CrashingUow:
        def __init__(self, inner, state):
            self._inner, self.audit = inner, CrashingAudit(inner.audit, state)
            self.experiments, self.media = inner.experiments, inner.media
            self.telemetry = inner.telemetry


    class CrashingStorage:
        def __init__(self, database):
            self._database = database
            self.experiments = database.experiments

        @contextmanager
        def unit_of_work(self):
            state = {}
            with self._database.unit_of_work() as uow:
                yield CrashingUow(uow, state)
                if state and point == "before_commit":
                    os._exit(42)
            if state and point == "after_commit":
                os._exit(42)


    harness = make_harness(
        root,
        tubes_of_rows(1, 2),
        config=fast_config(time_scale=36000.0),
        use_scaled_schedule=True,
        storage_wrapper=CrashingStorage,
    )
    harness.save_and_start()
    harness.finished()
    os._exit(0)
    """
)


@pytest.mark.parametrize(("point", "analyzed_rows"), [("before_commit", 0), ("after_commit", 1)])
def test_process_crash_during_row_commit(tmp_path: Path, point: str, analyzed_rows: int) -> None:
    """进程在结果事务提交前后被杀：结果和审计要么都有要么都没有；重启后标记为异常。"""
    script = tmp_path / "engine_crash.py"
    script.write_text(_CRASH_SCRIPT, encoding="utf-8")
    root = Path(__file__).parents[2]
    done = subprocess.run(
        [sys.executable, str(script), str(tmp_path), point],
        env={**os.environ, "PYTHONPATH": str(root)},
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 42, done.stdout + done.stderr

    database = open_database(tmp_path / "data" / "gel.db")
    try:
        assert verify_audit_chain(database).ok
        stored = database.experiments.get(EXP_ID)
        assert stored is not None
        assert stored.state in (S.ANALYZING, S.INCUBATING)
        analyzed = database.audit.query(
            AuditQuery(experiment_id=EXP_ID, action=AuditAction.ROW_ANALYZED), PageRequest()
        )
        assert analyzed.total == analyzed_rows
        assert len(stored.results) == 8 * analyzed_rows
        # 抓帧事务已经提交：入库的媒体文件都在磁盘上。
        images = database.media.list_for_experiment(EXP_ID, kind=MediaKind.IMAGE)
        assert len(images) >= 6 and all(Path(m.path).is_file() for m in images)

        # 重启恢复：标记为异常，不自动继续。
        h = make_harness(tmp_path / "restart")
        engine = ExperimentEngine(
            h.device,
            h.pipeline,
            h.analyzer,
            database,
            schedule_clock=ManualClock(),
            config=fast_config(),
        )
        recovered = engine.recover_interrupted()
        assert [r.experiment_id for r in recovered] == [EXP_ID]
        assert h.device.calls == []
        h.close()
        stored = database.experiments.get(EXP_ID)
        assert stored is not None and stored.state is S.FAULTED
        assert verify_audit_chain(database).ok
    finally:
        database.close()
