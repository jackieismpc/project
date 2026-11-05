"""试剂校验记录：追加、按批次汇总、分页，以及与审计在同一事务里提交。"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest

from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind
from gelinspect.infrastructure.db import Database
from gelinspect.interfaces.storage import PageRequest, ReagentCheckRecord

from .conftest import make_event

T = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)


def record(
    minutes: int,
    batch: str,
    *,
    kind: ReagentKind = ReagentKind.LAL_REAGENT,
    status: ReagentCheckStatus = ReagentCheckStatus.VALID,
    actor: str = "alice",
    registered: bool = True,
    expiry: date | None = date(2027, 6, 30),
) -> ReagentCheckRecord:
    return ReagentCheckRecord(
        checked_at=T + timedelta(minutes=minutes),
        actor=actor,
        kind=kind,
        batch_no=batch,
        product_code="GL-LAL-0.25",
        status=status,
        registered=registered,
        expiry_date=expiry,
        labeled_sensitivity=0.25 if kind is ReagentKind.LAL_REAGENT else None,
        message="备注",
        raw_code=f"GL|{batch}",
    )


def test_records_roundtrip_newest_first(database: Database) -> None:
    database.reagent_checks.add(record(0, "A"))
    database.reagent_checks.add(record(5, "B", status=ReagentCheckStatus.EXPIRED, expiry=None))
    page = database.reagent_checks.query(PageRequest(limit=10))
    assert page.total == 2
    newest, oldest = page.items
    assert (newest.batch_no, oldest.batch_no) == ("B", "A")
    assert newest.status is ReagentCheckStatus.EXPIRED and newest.expiry_date is None
    assert oldest.checked_at == T and oldest.expiry_date == date(2027, 6, 30)
    assert oldest.labeled_sensitivity == 0.25 and oldest.raw_code == "GL|A"
    assert oldest.record_id is not None and newest.record_id is not None
    assert newest.record_id > oldest.record_id


def test_batches_are_summarised_by_kind_and_batch_number(database: Database) -> None:
    repo = database.reagent_checks
    repo.add(record(0, "A", actor="alice"))
    repo.add(record(1, "A", actor="bob", status=ReagentCheckStatus.EXPIRED))
    repo.add(record(2, "A", kind=ReagentKind.WATER))  # 同批号不同种类是另一个批次
    repo.add(record(3, "B", registered=False, status=ReagentCheckStatus.NOT_REGISTERED))
    batches = repo.list_batches()
    assert [(b.kind, b.batch_no) for b in batches] == [
        (ReagentKind.LAL_REAGENT, "B"),
        (ReagentKind.WATER, "A"),
        (ReagentKind.LAL_REAGENT, "A"),
    ]
    latest_a = batches[2]
    assert latest_a.last_actor == "bob" and latest_a.check_count == 2
    assert latest_a.last_checked_at == T + timedelta(minutes=1)
    assert batches[0].registered is False
    assert len(repo.list_batches(limit=2)) == 2


def test_records_commit_with_the_audit_in_one_unit_of_work(database: Database) -> None:
    with database.unit_of_work() as uow:
        uow.reagent_checks.add(record(0, "A"))
        uow.audit.append(make_event(1))
    assert database.reagent_checks.query(PageRequest()).total == 1
    assert database.audit.verify_chain().checked == 1

    with pytest.raises(RuntimeError), database.unit_of_work() as uow:
        uow.reagent_checks.add(record(1, "B"))
        uow.audit.append(make_event(2))
        raise RuntimeError
    assert database.reagent_checks.query(PageRequest()).total == 1  # 回滚：两者都没有留下
    assert database.audit.verify_chain().checked == 1


def test_templates_are_available_inside_a_unit_of_work(database: Database) -> None:
    from gelinspect.domain.templates import BUILTIN_TEMPLATES

    template = next(iter(BUILTIN_TEMPLATES.values()))
    with database.unit_of_work() as uow:
        uow.templates.save(template, builtin=True)
        uow.audit.append(make_event(1))
    assert database.templates.get(template.template_id) == template
    with pytest.raises(RuntimeError), database.unit_of_work() as uow:
        uow.templates.save(dataclasses.replace(template, name="改名"), builtin=True)
        raise RuntimeError
    saved = database.templates.get(template.template_id)
    assert saved is not None and saved.name == template.name
