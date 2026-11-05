"""试剂扫码校验：过期、未备案、种类放错、读不出码、清单不可用都不放行，并写校验记录和审计。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction, ReagentCheckStatus, ReagentKind
from gelinspect.interfaces.storage import PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.reagent_service import ReagentService, ScanOutcome
from simulation.reagents import SAMPLE_CODES, SimReagentRegistry, SimTraceabilityClient

from .conftest import NOW, Stack, build_stack


def test_valid_code_passes_and_is_recorded(stack: Stack) -> None:
    session = stack.login("experimenter")
    scan = stack.reagents.scan(
        session, SAMPLE_CODES["lal_valid"], expected_kind=ReagentKind.LAL_REAGENT
    )
    assert scan.valid and not scan.locks and scan.outcome is ScanOutcome.VALID
    assert scan.batch_no == "LAL2606A" and scan.kind is ReagentKind.LAL_REAGENT
    assert scan.expiry_date is not None and scan.expiry_date.isoformat() == "2027-06-30"
    assert scan.days_remaining == (scan.expiry_date - NOW.date()).days
    assert scan.labeled_sensitivity == 0.25 and scan.registered
    batch = scan.to_batch()
    assert batch is not None and batch.check(NOW.date()) is ReagentCheckStatus.VALID
    records = stack.database.reagent_checks.query(PageRequest()).items
    assert [(r.batch_no, r.status, r.actor) for r in records] == [
        ("LAL2606A", ReagentCheckStatus.VALID, "experimenter")
    ]
    assert any("LAL2606A" in d and "valid" in d for d in stack.audit(AuditAction.REAGENT_CHECKED))


def test_water_code_without_an_expiry_uses_the_registry_expiry(stack: Stack) -> None:
    """试剂码里没有有效期时，用备案清单确认的有效期；不会因此变成「有效期未知」。"""
    scan = stack.reagents.evaluate(SAMPLE_CODES["water_valid"], ReagentKind.WATER)
    assert scan.valid and scan.expiry_date is not None
    assert scan.expiry_date.isoformat() == "2028-07-31"


@pytest.mark.parametrize(
    ("code", "kind", "outcome"),
    [
        (SAMPLE_CODES["lal_expired"], ReagentKind.LAL_REAGENT, ScanOutcome.EXPIRED),
        (SAMPLE_CODES["lal_unregistered"], ReagentKind.LAL_REAGENT, ScanOutcome.NOT_REGISTERED),
    ],
)
def test_expired_and_unregistered_lock_the_flow_and_write_the_audit(
    stack: Stack, code: str, kind: ReagentKind, outcome: ScanOutcome
) -> None:
    scan = stack.reagents.scan(stack.login("experimenter"), code, expected_kind=kind)
    assert scan.outcome is outcome and scan.locks and not scan.valid
    assert any("流程锁定" in d for d in stack.audit(AuditAction.REAGENT_CHECKED))
    record = stack.database.reagent_checks.query(PageRequest()).items[0]
    assert record.status in (ReagentCheckStatus.EXPIRED, ReagentCheckStatus.NOT_REGISTERED)


def test_expiry_is_judged_on_the_check_date(tmp_path: Path) -> None:
    built = build_stack(tmp_path)
    try:
        code = SAMPLE_CODES["lal_valid"]  # 有效期 2027-06-30（含当天）
        last_day = ReagentService(
            built.auth, built.database, parser=built.reagents._parser,
            registry=SimReagentRegistry(), clock=lambda: NOW.replace(year=2027, month=6, day=30),
        )  # fmt: skip
        assert last_day.evaluate(code).valid
        next_day = ReagentService(
            built.auth, built.database, parser=built.reagents._parser,
            registry=SimReagentRegistry(),
            clock=lambda: NOW.replace(year=2027, month=6, day=30) + timedelta(days=1),
        )  # fmt: skip
        assert next_day.evaluate(code).outcome is ScanOutcome.EXPIRED
    finally:
        built.database.close()


def test_wrong_kind_and_unreadable_codes_are_rejected_without_locking(stack: Stack) -> None:
    session = stack.login("experimenter")
    wrong = stack.reagents.scan(
        session, SAMPLE_CODES["water_valid"], expected_kind=ReagentKind.LAL_REAGENT
    )
    assert wrong.outcome is ScanOutcome.KIND_MISMATCH and not wrong.valid and not wrong.locks
    unreadable = stack.reagents.scan(session, "not a reagent code", expected_kind=ReagentKind.WATER)
    assert unreadable.outcome is ScanOutcome.UNREADABLE and not unreadable.locks
    # 没有校验结论，所以不写校验记录；但两次扫码都留了审计
    assert stack.database.reagent_checks.query(PageRequest()).total == 0
    assert len(stack.audit(AuditAction.REAGENT_CHECKED)) == 2


def test_an_unavailable_registry_locks_instead_of_passing(tmp_path: Path) -> None:
    built = build_stack(tmp_path, registry=SimReagentRegistry(available=False))
    try:
        scan = built.reagents.scan(
            built.login("experimenter"),
            SAMPLE_CODES["lal_valid"],
            expected_kind=ReagentKind.LAL_REAGENT,
        )
        assert scan.outcome is ScanOutcome.REGISTRY_UNAVAILABLE and scan.locks
    finally:
        built.database.close()


def test_without_a_parser_or_registry_nothing_can_pass(stack: Stack) -> None:
    bare = ReagentService(stack.auth, stack.database, clock=lambda: NOW)
    assert not bare.configured
    scan = bare.evaluate(SAMPLE_CODES["lal_valid"], ReagentKind.LAL_REAGENT)
    assert scan.outcome is ScanOutcome.NOT_CONFIGURED and scan.locks
    assert bare.sample_codes() == []
    assert not bare.government_status().configured


def test_missing_kinds_are_locked_when_checking_a_whole_experiment(stack: Stack) -> None:
    scans = stack.reagents.evaluate_all({ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_valid"]})
    assert scans[ReagentKind.LAL_REAGENT].valid
    assert scans[ReagentKind.ENDOTOXIN].outcome is ScanOutcome.NOT_CONFIGURED
    assert scans[ReagentKind.WATER].locks


def test_stored_batches_are_rechecked_against_todays_date(stack: Stack) -> None:
    scan = stack.reagents.evaluate(SAMPLE_CODES["lal_valid"], ReagentKind.LAL_REAGENT)
    batch = scan.to_batch()
    assert batch is not None
    assert stack.reagents.evaluate_batches([batch])[ReagentKind.LAL_REAGENT].valid
    expired = type(batch)(
        kind=batch.kind, batch_no=batch.batch_no, expiry_date=NOW.date() - timedelta(days=1),
        registered=True,
    )  # fmt: skip
    assert stack.reagents.evaluate_batches([expired])[ReagentKind.LAL_REAGENT].outcome is (
        ScanOutcome.EXPIRED
    )
    unregistered = type(batch)(
        kind=batch.kind, batch_no=batch.batch_no, expiry_date=batch.expiry_date, registered=False
    )
    assert stack.reagents.evaluate_batches([unregistered])[ReagentKind.LAL_REAGENT].outcome is (
        ScanOutcome.NOT_REGISTERED
    )


def test_permissions(stack: Stack) -> None:
    code = SAMPLE_CODES["lal_valid"]
    for role in ("analyst", "admin"):
        with pytest.raises(PermissionDeniedError):
            stack.reagents.scan(stack.login(role), code)  # 录入和校验需要 reagent_manage
    # 查询、批次列表和校验记录所有角色都能看
    for role in ("analyst", "admin", "experimenter"):
        session = stack.login(role)
        assert stack.reagents.lookup(session, code).valid
        stack.reagents.list_batches(session)
        stack.reagents.recent_checks(session)
    with pytest.raises(PermissionDeniedError):
        stack.reagents.lookup(None, code)
    assert stack.database.reagent_checks.query(PageRequest()).total == 0  # 查询不写记录


def test_batch_list_and_check_log(stack: Stack) -> None:
    session = stack.login("experimenter")
    for key in ("lal_valid", "lal_expired", "endotoxin_valid", "lal_valid"):
        stack.reagents.scan(session, SAMPLE_CODES[key])
    batches = stack.reagents.list_batches(session)
    assert {(b.batch_no, b.check_count) for b in batches} == {
        ("LAL2606A", 2), ("LAL2501B", 1), ("CSE2605A", 1),
    }  # fmt: skip
    assert stack.reagents.recent_checks(session).total == 4
    assert stack.reagents.days_remaining(None) is None
    assert stack.reagents.days_remaining(NOW.date()) == 0


def test_sample_codes_and_government_placeholder(stack: Stack) -> None:
    lal = stack.reagents.sample_codes(ReagentKind.LAL_REAGENT)
    assert len(lal) == 3
    assert all(raw.split("|")[2] == "lal_reagent" for _label, raw in lal)
    assert len(stack.reagents.sample_codes()) == 5
    assert not stack.reagents.government_status().configured
    wired = ReagentService(
        stack.auth, stack.database, government=SimTraceabilityClient(), clock=lambda: NOW
    )
    status = wired.government_status()
    assert status.configured and not status.enabled
