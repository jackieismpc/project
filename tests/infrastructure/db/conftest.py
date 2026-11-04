"""数据层测试共用的夹具与构造函数。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from gelinspect.domain.entities import (
    AuditEvent,
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
)
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentKind,
    Role,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.infrastructure.db import Database, open_database

T0 = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = open_database(tmp_path / "gel.db")
    yield db
    db.close()


def make_event(
    index: int = 0,
    *,
    actor: str = "alice",
    action: AuditAction = AuditAction.LOGIN,
    detail: str | None = None,
    experiment_id: str | None = None,
    at: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        occurred_at=at or T0 + timedelta(seconds=index),
        actor=actor,
        actor_role=Role.ANALYST,
        action=action,
        detail=detail if detail is not None else f"操作 {index}",
        experiment_id=experiment_id,
    )


def make_result(
    tube_no: int,
    *,
    state: GelState | None = GelState.FULLY_GELLED,
    status: TubeAnalysisStatus = TubeAnalysisStatus.OK,
) -> TubeResult:
    return TubeResult.from_analysis(
        tube_no=tube_no,
        status=status,
        state=state,
        mapping=GelStateMapping(),
        score=0.8,
        confidence=0.95,
        features={
            "area_before": 1200.0,
            "delta_area_pct": -3.5,
            "centroid_before": (10.5, 20.25),
            "gel_missing_before": False,
            "delta_s": None,
        },
        model_version="m-1.0",
        config_version="c-7",
        elapsed_ms=812.5,
    )


def make_experiment(
    experiment_id: str = "E1",
    *,
    experiment_type: ExperimentType = ExperimentType.SENSITIVITY_CONFIRMATION,
    created_at: datetime = T0,
    tube_count: int = 4,
    batch_no: str = "LAL-001",
    sample_batch_no: str | None = "S-001",
    with_results: bool = True,
) -> Experiment:
    experiment = Experiment(
        experiment_id=experiment_id,
        experiment_type=experiment_type,
        template_id="builtin.limit",
        instrument_no="GI-01",
        operator="alice",
        created_at=created_at,
        assignments=tuple(
            TubeAssignment(
                tube_no=no,
                sample=Sample(f"s{no}", f"样品{no}", serial_no=f"SN{no}", batch_no=sample_batch_no),
                role=TubeRole.TEST,
                dilution_factor=2.0,
                spiked_endotoxin=0.5,
            )
            for no in range(1, tube_count + 1)
        ),
        reagents=(
            ReagentBatch(ReagentKind.LAL_REAGENT, batch_no, date(2027, 1, 31), True, 0.25),
            ReagentBatch(ReagentKind.ENDOTOXIN, "CSE-9", date(2027, 6, 1), True),
            ReagentBatch(ReagentKind.WATER, "W-3", None, False),
        ),
    )
    if with_results:
        experiment.results = {no: make_result(no) for no in range(1, tube_count + 1)}
    return experiment


__all__ = ["T0", "Interpretation", "make_event", "make_experiment", "make_result"]
