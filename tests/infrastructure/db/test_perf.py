"""性能：10 万条审计 + 1 万管结果，分页查询应小于 1 秒。

默认运行；整组耗时约十余秒。用 ``pytest -m "not perf"`` 跳过。
实测数字用 ``pytest -s tests/infrastructure/db/test_perf.py`` 查看。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.interfaces.storage import AuditQuery, ExperimentQuery, PageRequest

from .conftest import T0, make_event, make_experiment

pytestmark = pytest.mark.perf

AUDIT_COUNT = 100_000
EXPERIMENT_COUNT = 125
TUBES_PER_EXPERIMENT = 80  # 125 × 80 = 10000 管
LIMIT_SECONDS = 1.0
ACTORS = [f"user{i:02d}" for i in range(20)]
ACTIONS = list(AuditAction)


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Database]:
    path: Path = tmp_path_factory.mktemp("perf") / "big.db"
    db = open_database(path)
    started = time.perf_counter()
    for start in range(0, AUDIT_COUNT, 10_000):
        db.audit.append_many(
            [
                make_event(
                    i,
                    actor=ACTORS[i % len(ACTORS)],
                    action=ACTIONS[i % len(ACTIONS)],
                    detail=f"记录 {i} 批号 LAL-{i % 500:03d}",
                    experiment_id=f"E{i % EXPERIMENT_COUNT:03d}",
                )
                for i in range(start, start + 10_000)
            ]
        )
    audit_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for n in range(EXPERIMENT_COUNT):
        experiment = make_experiment(
            f"E{n:03d}",
            experiment_type=list(ExperimentType)[n % 5],
            created_at=T0 + timedelta(hours=n),
            tube_count=TUBES_PER_EXPERIMENT,
            batch_no=f"LAL-{n % 25:03d}",
        )
        experiment.state = [ExperimentState.SIGNED, ExperimentState.COMPLETED][n % 2]
        db.experiments.save(experiment)
    result_seconds = time.perf_counter() - started
    print(
        f"\n[perf] 写入 {AUDIT_COUNT} 条审计 {audit_seconds:.2f}s；"
        f"{EXPERIMENT_COUNT * TUBES_PER_EXPERIMENT} 管结果 {result_seconds:.2f}s"
    )
    yield db
    db.close()


def timed(label: str, call: Callable[[], object], *, limit: float = LIMIT_SECONDS) -> object:
    started = time.perf_counter()
    value = call()
    elapsed = time.perf_counter() - started
    print(f"[perf] {label}: {elapsed * 1000:.1f} ms")
    assert elapsed < limit, f"{label} 用时 {elapsed:.3f}s，超过 {limit}s"
    return value


def test_dataset_sizes(big: Database) -> None:
    assert big.audit.query(AuditQuery(), PageRequest(limit=1)).total == AUDIT_COUNT
    summary = big.experiments.query(ExperimentQuery(), PageRequest(limit=1))
    assert summary.total == EXPERIMENT_COUNT


def test_audit_page_queries(big: Database) -> None:
    first = timed("审计 首页 50 条（无过滤）", lambda: big.audit.query(AuditQuery(), PageRequest()))
    assert first.total == AUDIT_COUNT  # type: ignore[attr-defined]
    timed(
        "审计 深翻页 offset=50000",
        lambda: big.audit.query(AuditQuery(), PageRequest(offset=50_000, limit=50)),
    )
    timed(
        "审计 按操作者",
        lambda: big.audit.query(AuditQuery(actor="user07"), PageRequest(offset=1000)),
    )
    timed(
        "审计 按时间段（1 小时）",
        lambda: big.audit.query(
            AuditQuery(start=T0 + timedelta(hours=5), end=T0 + timedelta(hours=6)), PageRequest()
        ),
    )
    timed(
        "审计 按关键字（全表子串匹配）",
        lambda: big.audit.query(AuditQuery(keyword="LAL-123"), PageRequest()),
    )
    timed(
        "审计 组合：操作者+事件+时间",
        lambda: big.audit.query(
            AuditQuery(
                actor="user03",
                action=ACTIONS[3],
                start=T0,
                end=T0 + timedelta(hours=20),
            ),
            PageRequest(),
        ),
    )


def test_history_page_queries(big: Database) -> None:
    timed("历史 首页", lambda: big.experiments.query(ExperimentQuery(), PageRequest()))
    timed(
        "历史 按类型",
        lambda: big.experiments.query(
            ExperimentQuery(experiment_type=ExperimentType.LIMIT), PageRequest()
        ),
    )
    timed(
        "历史 按批号",
        lambda: big.experiments.query(ExperimentQuery(batch_no="LAL-007"), PageRequest()),
    )
    timed(
        "历史 按状态+时间",
        lambda: big.experiments.query(
            ExperimentQuery(state=ExperimentState.SIGNED, start=T0, end=T0 + timedelta(hours=60)),
            PageRequest(),
        ),
    )
    timed("读取一个试验（80 管）", lambda: big.experiments.get("E010"))


def test_full_chain_verification(big: Database) -> None:
    result = timed("校验 10 万条哈希链", lambda: verify_audit_chain(big), limit=15.0)
    assert result.ok  # type: ignore[attr-defined]
