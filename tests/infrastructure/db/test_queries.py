"""分页与过滤。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.infrastructure.db import Database
from gelinspect.interfaces.storage import (
    MAX_PAGE_SIZE,
    AuditQuery,
    ExperimentQuery,
    PageRequest,
)

from .conftest import T0, make_event, make_experiment


@pytest.fixture
def audited(database: Database) -> Database:
    events = []
    for i in range(100):
        events.append(
            make_event(
                i,
                actor="alice" if i % 2 == 0 else "bob",
                action=AuditAction.LOGIN if i % 4 < 2 else AuditAction.EXPERIMENT_CREATED,
                detail=f"第 {i} 步 {'含100%进度' if i == 10 else '普通'}"
                + (" under_score" if i == 20 else ""),
                experiment_id="E-7" if i % 10 == 0 else None,
            )
        )
    database.audit.append_many(events)
    return database


def test_page_request_validation() -> None:
    for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": MAX_PAGE_SIZE + 1}):
        with pytest.raises(ValueError, match="offset|limit"):
            PageRequest(**kwargs)


def test_audit_pagination_is_newest_first_without_overlap(audited: Database) -> None:
    seen: list[int] = []
    for offset in range(0, 100, 30):
        page = audited.audit.query(AuditQuery(), PageRequest(offset=offset, limit=30))
        assert page.total == 100
        seen.extend(record.seq for record in page.items)
    assert seen == list(range(100, 0, -1))
    beyond = audited.audit.query(AuditQuery(), PageRequest(offset=500, limit=10))
    assert (beyond.items, beyond.total) == ((), 100)


def test_audit_filter_by_time_range(audited: Database) -> None:
    query = AuditQuery(start=T0 + timedelta(seconds=10), end=T0 + timedelta(seconds=20))
    page = audited.audit.query(query, PageRequest(limit=100))
    assert page.total == 10
    assert [r.event.occurred_at for r in page.items][-1] == T0 + timedelta(seconds=10)
    assert [r.event.occurred_at for r in page.items][0] == T0 + timedelta(seconds=19)


def test_audit_filter_by_actor_action_and_experiment(audited: Database) -> None:
    assert audited.audit.query(AuditQuery(actor="bob"), PageRequest()).total == 50
    assert audited.audit.query(AuditQuery(actor="nobody"), PageRequest()).total == 0
    assert audited.audit.query(AuditQuery(action=AuditAction.LOGIN), PageRequest()).total == 50
    assert audited.audit.query(AuditQuery(experiment_id="E-7"), PageRequest()).total == 10
    combined = AuditQuery(actor="alice", action=AuditAction.EXPERIMENT_CREATED)
    page = audited.audit.query(combined, PageRequest(limit=100))
    assert page.total == 25
    assert all(r.event.actor == "alice" for r in page.items)


def test_audit_keyword_search(audited: Database) -> None:
    assert audited.audit.query(AuditQuery(keyword="第 42 步"), PageRequest()).total == 1
    assert audited.audit.query(AuditQuery(keyword="普通"), PageRequest()).total == 99
    # 关键字也匹配试验标识，且不区分大小写
    assert audited.audit.query(AuditQuery(keyword="e-7"), PageRequest()).total == 10
    # LIKE 通配符按字面匹配
    assert audited.audit.query(AuditQuery(keyword="100%"), PageRequest()).total == 1
    assert audited.audit.query(AuditQuery(keyword="%"), PageRequest()).total == 1
    assert audited.audit.query(AuditQuery(keyword="_"), PageRequest()).total == 1
    assert audited.audit.query(AuditQuery(keyword="不存在"), PageRequest()).total == 0


def test_audit_query_uses_indexes(audited: Database) -> None:
    def plan(sql: str) -> str:
        with audited.session() as session:
            return " ".join(
                str(row[3]) for row in session.execute(text("EXPLAIN QUERY PLAN " + sql))
            )

    assert "ix_audit_events_actor_seq" in plan(
        "SELECT * FROM audit_events WHERE actor='alice' ORDER BY seq DESC LIMIT 10"
    )
    assert "ix_audit_events_occurred_at" in plan(
        "SELECT * FROM audit_events WHERE occurred_at >= '2026' AND occurred_at < '2027'"
    )
    assert "ix_audit_events_experiment_id_seq" in plan(
        "SELECT * FROM audit_events WHERE experiment_id='E-7' ORDER BY seq DESC"
    )


@pytest.fixture
def history(database: Database) -> Database:
    specs = [
        ("E1", ExperimentType.SENSITIVITY_CONFIRMATION, 0, "LAL-A", "S-1", ExperimentState.SIGNED),
        ("E2", ExperimentType.LIMIT, 1, "LAL-A", "S-2", ExperimentState.COMPLETED),
        ("E3", ExperimentType.LIMIT, 2, "LAL-B", "S-3", ExperimentState.PENDING_REVIEW),
        ("E4", ExperimentType.SEMI_QUANTITATIVE, 3, "LAL-C", "LAL-B", ExperimentState.STOPPED),
        ("E5", ExperimentType.LIMIT, 4, "LAL-C", None, ExperimentState.SIGNED),
    ]
    for experiment_id, kind, day, lal, sample_batch, state in specs:
        experiment = make_experiment(
            experiment_id,
            experiment_type=kind,
            created_at=T0 + timedelta(days=day),
            batch_no=lal,
            sample_batch_no=sample_batch,
            tube_count=3,
        )
        experiment.state = state
        database.experiments.save(experiment)
    return database


def ids(database: Database, query: ExperimentQuery, page: PageRequest | None = None) -> list[str]:
    return [s.experiment_id for s in database.experiments.query(query, page or PageRequest()).items]


def test_history_default_order_and_summary(history: Database) -> None:
    page = history.experiments.query(ExperimentQuery(), PageRequest())
    assert [s.experiment_id for s in page.items] == ["E5", "E4", "E3", "E2", "E1"]
    assert page.total == 5
    first = page.items[0]
    assert (first.tube_count, first.state, first.operator) == (3, ExperimentState.SIGNED, "alice")


def test_history_filters(history: Database) -> None:
    assert ids(history, ExperimentQuery(experiment_type=ExperimentType.LIMIT)) == ["E5", "E3", "E2"]
    assert ids(history, ExperimentQuery(state=ExperimentState.SIGNED)) == ["E5", "E1"]
    day = timedelta(days=1)
    assert ids(history, ExperimentQuery(start=T0 + day, end=T0 + 3 * day)) == ["E3", "E2"]
    assert ids(history, ExperimentQuery(operator="alice")) and not ids(
        history, ExperimentQuery(operator="zed")
    )
    # 批号同时匹配试剂批号与样品批号
    assert ids(history, ExperimentQuery(batch_no="LAL-A")) == ["E2", "E1"]
    assert ids(history, ExperimentQuery(batch_no="LAL-B")) == ["E4", "E3"]
    assert ids(history, ExperimentQuery(batch_no="S-2")) == ["E2"]
    assert ids(history, ExperimentQuery(batch_no="none")) == []
    combined = ExperimentQuery(
        experiment_type=ExperimentType.LIMIT, state=ExperimentState.SIGNED, batch_no="LAL-C"
    )
    assert ids(history, combined) == ["E5"]


def test_history_pagination(history: Database) -> None:
    first = history.experiments.query(ExperimentQuery(), PageRequest(offset=0, limit=2))
    second = history.experiments.query(ExperimentQuery(), PageRequest(offset=2, limit=2))
    third = history.experiments.query(ExperimentQuery(), PageRequest(offset=4, limit=2))
    assert [s.experiment_id for p in (first, second, third) for s in p.items] == [
        "E5",
        "E4",
        "E3",
        "E2",
        "E1",
    ]
    assert first.total == second.total == third.total == 5
