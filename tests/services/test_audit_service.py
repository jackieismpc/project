"""审计追踪服务：按时间段、操作者、关键字查询，分页；校验哈希链；查看审计本身写审计。"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.interfaces.storage import AuditQuery, ChainFailure, PageRequest
from gelinspect.services.audit_service import AuditFilter, AuditService
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode

from .conftest import NOW, Stack


@pytest.fixture
def audit(stack: Stack) -> AuditService:
    return AuditService(stack.auth, stack.database, clock=lambda: NOW)


def seed(stack: Stack, count: int = 120) -> None:
    stack.database.audit.append_many(
        [
            AuditEvent(
                occurred_at=NOW - timedelta(hours=count - i),
                actor="alice" if i % 3 else "bob",
                actor_role=Role.ANALYST,
                action=AuditAction.LOGIN if i % 2 else AuditAction.TEMPLATE_CHANGED,
                detail=f"第 {i} 条 批号 LAL-{i % 7}",
                experiment_id=f"E{i % 4}",
            )
            for i in range(count)
        ]
    )


def test_experimenter_cannot_view_audit(stack: Stack, audit: AuditService) -> None:
    session = stack.login("experimenter")
    for call in (
        lambda: audit.query(session, AuditFilter(), PageRequest()),
        lambda: audit.verify_chain(session),
        lambda: audit.actors(session),
    ):
        with pytest.raises(PermissionDeniedError):
            call()
    assert any("audit_view" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))


@pytest.mark.parametrize("who", ["admin", "analyst"])
def test_admin_and_analyst_can_query(stack: Stack, audit: AuditService, who: str) -> None:
    seed(stack)
    page = audit.query(stack.login(who), AuditFilter(), PageRequest(limit=50))
    assert page.total >= 120 and len(page.items) == 50
    assert page.items[0].seq > page.items[1].seq  # 最新在前


def test_filters_by_actor_keyword_and_time_and_paginates(stack: Stack, audit: AuditService) -> None:
    seed(stack)
    session = stack.login("analyst")
    bob = audit.query(session, AuditFilter(actor="bob"), PageRequest(limit=1000))
    assert bob.total == 40 and {r.event.actor for r in bob.items} == {"bob"}
    by_word = audit.query(session, AuditFilter(keyword="lal-3"), PageRequest(limit=1000))
    assert by_word.total == len([i for i in range(120) if i % 7 == 3])  # 本次查看写在查询之后
    window = audit.query(
        session,
        AuditFilter(start=NOW - timedelta(hours=10), end=NOW - timedelta(hours=5)),
        PageRequest(limit=1000),
    )
    assert window.total == 5
    combined = audit.query(
        session, AuditFilter(actor="alice", keyword="批号 LAL-1"), PageRequest(limit=1000)
    )
    assert all(r.event.actor == "alice" and "LAL-1" in r.event.detail for r in combined.items)
    first = audit.query(session, AuditFilter(), PageRequest(offset=0, limit=50))
    # 第一页之后写了一条“查看审计追踪”：用第一页看到的最大序号固定范围，第二页不被它顶歪
    bound = AuditFilter(max_seq=first.items[0].seq)
    second = audit.query(session, bound, PageRequest(offset=50, limit=50))
    assert not {r.seq for r in first.items} & {r.seq for r in second.items}
    drifted = audit.query(session, AuditFilter(), PageRequest(offset=50, limit=50))
    assert {r.seq for r in first.items} & {r.seq for r in drifted.items}  # 不固定范围就会重复
    assert min(r.seq for r in first.items) > max(r.seq for r in second.items)


def test_latest_seq_fixes_the_range_of_a_query(stack: Stack, audit: AuditService) -> None:
    session = stack.login("analyst")
    assert audit.latest_seq(session) == stack.database.audit.latest_seq() >= 0
    seed(stack, 30)
    bound = audit.latest_seq(session)
    assert bound == stack.database.audit.query(AuditQuery(), PageRequest(limit=1)).items[0].seq
    before = audit.query(session, AuditFilter(max_seq=bound), PageRequest(limit=1000))
    seed(stack, 5)  # 之后写入的记录
    after = audit.query(session, AuditFilter(max_seq=bound), PageRequest(limit=1000))
    assert after.total == before.total and [r.seq for r in after.items] == [
        r.seq for r in before.items
    ]
    assert audit.latest_seq(session) > bound


def test_latest_seq_needs_the_audit_permission(stack: Stack, audit: AuditService) -> None:
    with pytest.raises(PermissionDeniedError):
        audit.latest_seq(stack.login("experimenter"))


def test_blank_conditions_are_ignored_and_bad_ranges_are_rejected(
    stack: Stack, audit: AuditService
) -> None:
    session = stack.login("admin")
    assert AuditFilter(actor="  ", keyword="").to_query() == AuditQuery()
    with pytest.raises(ServiceError) as caught:
        audit.query(session, AuditFilter(start=NOW, end=NOW - timedelta(days=1)), PageRequest())
    assert caught.value.code is ServiceErrorCode.AUDIT_QUERY_INVALID


def test_viewing_the_audit_is_itself_audited_once_per_query(
    stack: Stack, audit: AuditService
) -> None:
    seed(stack, 130)
    session = stack.login("analyst")
    audit.query(session, AuditFilter(keyword="LAL"), PageRequest(offset=0, limit=50))
    audit.query(session, AuditFilter(keyword="LAL"), PageRequest(offset=50, limit=50))  # 翻页
    audit.query(session, AuditFilter(keyword="LAL"), PageRequest(offset=100, limit=50))
    views = stack.audit(AuditAction.AUDIT_VIEWED)
    assert views == ["查看审计追踪：关键字=LAL"]
    audit.query(session, AuditFilter(), PageRequest(offset=0, limit=50))
    assert stack.audit(AuditAction.AUDIT_VIEWED)[-1] == "查看审计追踪：无条件"
    page = stack.database.audit.query(AuditQuery(action=AuditAction.AUDIT_VIEWED), PageRequest())
    assert {r.event.actor for r in page.items} == {"analyst"}


def test_actors_list_all_users_plus_the_system(stack: Stack, audit: AuditService) -> None:
    names = audit.actors(stack.login("admin"))
    assert {"admin", "experimenter", "analyst", "system"} <= set(names)


def test_verify_passes_on_an_intact_chain_reports_progress_and_audits(
    stack: Stack, audit: AuditService
) -> None:
    seed(stack, 50)
    seen: list[tuple[int, int]] = []
    result = audit.verify_chain(
        stack.login("admin"), lambda done, total: seen.append((done, total))
    )
    assert result.ok and result.first_bad_seq is None
    assert seen and seen[-1][0] <= seen[-1][1]
    assert all(total >= done for done, total in seen)
    assert stack.audit(AuditAction.AUDIT_VERIFIED)[-1].startswith("校验审计完整性：通过，共")
    # 校验之后追加的这条不影响下一次校验
    assert audit.verify_chain(stack.login("admin")).ok


def raw(stack: Stack, sql: str, *params: object) -> None:
    path = stack.database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        connection.execute(sql, params)


def test_verify_detects_tampering_and_names_the_first_bad_record(
    stack: Stack, audit: AuditService
) -> None:
    seed(stack, 30)
    admin = stack.login("admin")
    seq = 12
    raw(stack, "UPDATE audit_events SET detail = '被篡改' WHERE seq = ?", seq)
    result = audit.verify_chain(admin)
    assert not result.ok and result.first_bad_seq == seq
    assert result.failure is ChainFailure.HASH_MISMATCH
    line = stack.audit(AuditAction.AUDIT_VERIFIED)[-1]
    assert f"第一处异常在序号 {seq}" in line and "未通过" in line


def test_verify_detects_a_deleted_record(stack: Stack, audit: AuditService) -> None:
    seed(stack, 30)
    raw(stack, "DELETE FROM audit_events WHERE seq = 9")
    result = audit.verify_chain(stack.login("analyst"))
    assert not result.ok and result.first_bad_seq == 9 and result.failure is ChainFailure.MISSING
