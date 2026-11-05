"""不能签署的情况，以及签署中途失败时不留半成品。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from gelinspect.domain.entities import TubeResult
from gelinspect.domain.enums import (
    AuditAction,
    ConclusionVerdict,
    GelState,
    Interpretation,
    TubeAnalysisStatus,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.signing import SigningBlocker
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.reporting.report_service import SigningNotAllowedError
from simulation.sample_data import SENSITIVITY_STATES, sensitivity_experiment
from tests.services.conftest import PASSWORDS, Stack

from .conftest import make_completed, make_service


def blockers(error: SigningNotAllowedError) -> list[SigningBlocker]:
    return [block.blocker for block in error.blocks]


def sign_blocked(stack: Stack, tmp_path: Path, experiment_id: str) -> SigningNotAllowedError:
    service, _ = make_service(stack, tmp_path)
    with pytest.raises(SigningNotAllowedError) as caught:
        service.sign(stack.login("analyst"), experiment_id, PASSWORDS["analyst"])
    # 被拒绝时不留任何文件、记录，状态不变
    assert not (tmp_path / "data" / "reports").exists()
    assert stack.database.reports.list_for_experiment(experiment_id) == []
    assert stack.audit(AuditAction.EXPERIMENT_SIGNED) == []
    return caught.value


def test_pending_review_cannot_be_signed(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    experiment = sensitivity_experiment()
    for event in (
        ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK,
        ExperimentEvent.REAGENT_CHECK_PASSED,
        ExperimentEvent.START,
        ExperimentEvent.ROW_DUE,
        ExperimentEvent.ROW_CAPTURED,
    ):
        experiment.apply(event)
    for no, state in SENSITIVITY_STATES.items():
        experiment.record_result(
            TubeResult.from_analysis(
                tube_no=no, status=TubeAnalysisStatus.OK, state=state, mapping=GelStateMapping()
            )
        )
    experiment.apply(ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW)
    stack.database.experiments.save(experiment)
    error = sign_blocked(stack, tmp_path, "EXP-SAMPLE-0001")
    assert blockers(error) == [SigningBlocker.PENDING_REVIEW]
    assert error.blocks[0].tubes == (7,)
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.PENDING_REVIEW


def test_pending_conclusion_cannot_be_signed(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    # 模板布局未确认：规则读不了，结论是「待定」
    make_completed(stack, layout_confirmed=False)
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    assert stored.conclusion is not None and stored.conclusion.verdict is ConclusionVerdict.PENDING
    error = sign_blocked(stack, tmp_path, "EXP-SAMPLE-0001")
    assert blockers(error) == [SigningBlocker.VERDICT_PENDING]
    assert error.conclusion is not None and error.conclusion.unmet  # 说明原因


def test_invalid_conclusion_cannot_be_signed_and_explains_why(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    states = {**SENSITIVITY_STATES, 17: GelState.FULLY_GELLED}  # 阴性对照出现阳性
    make_completed(stack, states=states)
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.conclusion is not None
    assert stored.conclusion.verdict is ConclusionVerdict.INVALID
    error = sign_blocked(stack, tmp_path, "EXP-SAMPLE-0001")
    assert blockers(error) == [SigningBlocker.VERDICT_INVALID]
    assert error.conclusion is not None
    assert any("阴性对照" in note.text for note in error.conclusion.unmet)
    # 预览里同样给出原因，界面据此提示
    service, _ = make_service(stack, tmp_path)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    assert not preview.can_sign
    assert preview.conclusion is not None and preview.conclusion.verdict is (
        ConclusionVerdict.INVALID
    )


def test_there_is_no_switch_that_bypasses_the_verdict_rule() -> None:
    from gelinspect.services.config import ReportSection

    names = set(ReportSection.model_fields)
    assert not {n for n in names if "sign" in n or "bypass" in n or "allow" in n}


def test_running_and_stopped_experiments_cannot_be_signed(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    experiment = sensitivity_experiment()
    stack.database.experiments.save(experiment)  # 草稿
    error = sign_blocked(stack, tmp_path, "EXP-SAMPLE-0001")
    assert blockers(error) == [SigningBlocker.NOT_COMPLETED]


def test_completed_without_a_recorded_conclusion_cannot_be_signed(
    signing_stack: Stack, tmp_path: Path
) -> None:
    from sqlalchemy import text

    stack = signing_stack
    make_completed(stack)
    with stack.database.session() as session:  # 模拟 P10 之前完成、没有结论的旧试验
        session.execute(text("DELETE FROM experiment_conclusions"))
    error = sign_blocked(stack, tmp_path, "EXP-SAMPLE-0001")
    assert blockers(error) == [SigningBlocker.NO_CONCLUSION]


# ---------------------------------------------------------------------------- 中途失败


class InjectedError(RuntimeError):
    pass


def assert_nothing_left(stack: Stack, tmp_path: Path) -> None:
    """签署失败后：没有任何文件（含临时文件）、没有报告记录、状态不变、没有签署审计。"""
    reports = tmp_path / "data" / "reports"
    leftovers = [p for p in reports.rglob("*") if p.is_file()] if reports.exists() else []
    assert leftovers == []
    assert stack.database.reports.list_for_experiment("EXP-SAMPLE-0001") == []
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None
    assert stored.state is ExperimentState.COMPLETED
    assert stored.signed_by is None and stored.signed_at is None
    assert stack.audit(AuditAction.EXPERIMENT_SIGNED) == []
    assert not [d for d in stack.audit(AuditAction.EXPERIMENT_STATE_CHANGED) if "signed" in d]


def test_failure_while_rendering_leaves_no_half_finished_report(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise InjectedError("排版失败")

    service._renderer.render_pdf = broken  # type: ignore[method-assign]
    with pytest.raises(ServiceError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.SIGN_FAILED
    assert_nothing_left(stack, tmp_path)


def test_failure_while_signing_leaves_no_half_finished_report(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)

    def broken(*_args: Any, **_kwargs: Any) -> bytes:
        raise InjectedError("签名失败")

    service, _ = make_service(stack, tmp_path, signer=broken)
    with pytest.raises(ServiceError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.SIGN_FAILED
    assert_nothing_left(stack, tmp_path)


def test_a_signature_that_fails_the_self_check_is_never_stored(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)

    def unsigned(pdf: bytes, *_args: Any, **_kwargs: Any) -> bytes:
        return pdf  # 假装签了名，其实没签

    service, _ = make_service(stack, tmp_path, signer=unsigned)
    with pytest.raises(ServiceError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.SIGN_FAILED
    assert_nothing_left(stack, tmp_path)


def test_failure_writing_the_file_leaves_nothing(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack)
    data = tmp_path / "data"
    data.mkdir()
    (data / "reports").write_text("占位：让 reports 目录建不出来")
    service, _ = make_service(stack, tmp_path)
    with pytest.raises(ServiceError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.SIGN_FAILED
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    assert stack.database.reports.list_for_experiment("EXP-SAMPLE-0001") == []


class _Repo:
    """仓储的代理：指定的方法抛异常，其余原样转发。"""

    def __init__(self, target: Any, failing: str) -> None:
        self._target = target
        self._failing = failing

    def __getattr__(self, name: str) -> Any:
        if name == self._failing:

            def boom(*_args: Any, **_kwargs: Any) -> Any:
                raise InjectedError(f"{name} 写入失败")

            return boom
        return getattr(self._target, name)


class _Uow:
    """工作单元的代理：让其中一个仓储的某个方法失败。"""

    def __init__(self, target: Any, repo: str, method: str) -> None:
        self._target = target
        self._repo = repo
        self._method = method

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        return _Repo(value, self._method) if name == self._repo else value


def fail_in_unit_of_work(
    monkeypatch: pytest.MonkeyPatch, stack: Stack, repo: str, method: str
) -> None:
    """让工作单元里 ``repo.method`` 抛异常（模拟数据库写入失败）。"""
    real = stack.database.unit_of_work

    @contextmanager
    def patched() -> Any:
        with real() as uow:
            yield _Uow(uow, repo, method)

    monkeypatch.setattr(stack.database, "unit_of_work", patched)


@pytest.mark.parametrize(
    ("repo", "method"),
    [("audit", "append_many"), ("reports", "add"), ("experiments", "record_signature")],
)
def test_failure_inside_the_unit_of_work_rolls_everything_back(
    signing_stack: Stack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo: str,
    method: str,
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    fail_in_unit_of_work(monkeypatch, stack, repo, method)
    with pytest.raises(InjectedError):
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert_nothing_left(stack, tmp_path)


def test_failure_at_the_final_step_removes_the_already_moved_file(
    signing_stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件已放到最终位置、提交才失败：文件被删除，数据库不变。"""
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    real_uow = stack.database.unit_of_work

    @contextmanager
    def commit_fails() -> Any:
        with real_uow() as uow:
            yield uow
            assert (tmp_path / "data/reports/EXP-SAMPLE-0001/EXP-SAMPLE-0001.pdf").exists()
            raise InjectedError("提交失败")

    monkeypatch.setattr(stack.database, "unit_of_work", commit_fails)
    with pytest.raises(InjectedError):
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert_nothing_left(stack, tmp_path)


def test_busy_database_propagates_and_changes_nothing(
    signing_stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)

    @contextmanager
    def busy() -> Any:
        raise StorageBusyError("忙")
        yield  # pragma: no cover

    monkeypatch.setattr(stack.database, "unit_of_work", busy)
    with pytest.raises(StorageBusyError):
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert_nothing_left(stack, tmp_path)


def test_state_changed_elsewhere_is_a_conflict_not_a_half_signature(
    signing_stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    real = stack.database.unit_of_work

    @contextmanager
    def competing_writer() -> Any:
        with real() as uow:
            # 另一个操作抢在签署之前改了状态：签署带着「预期状态」写入时发现冲突
            uow.experiments.update_state("EXP-SAMPLE-0001", ExperimentState.STOPPED)
            yield uow

    monkeypatch.setattr(stack.database, "unit_of_work", competing_writer)
    with pytest.raises(ServiceError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.SIGN_CONFLICT
    reports = tmp_path / "data" / "reports"
    assert [p for p in reports.rglob("*") if p.is_file()] == []
    assert stack.database.reports.list_for_experiment("EXP-SAMPLE-0001") == []
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED  # 事务回滚
    assert stored.signed_by is None


def test_review_cannot_change_results_after_signing_even_through_the_domain(
    signing_stack: Stack, tmp_path: Path
) -> None:
    from gelinspect.domain.errors import TransitionGuardError

    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None
    with pytest.raises(TransitionGuardError):
        stored.review_tube(
            7,
            interpretation=Interpretation.NEGATIVE,
            reviewed_by="analyst",
            reviewed_at=stored.signed_at or stored.created_at,
        )
    with pytest.raises(TransitionGuardError):
        stored.record_result(stored.results[7])
