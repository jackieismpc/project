"""试验仓储：试验聚合、管位安排、试剂批次、单管结果与历史查询。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    delete,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Session

from gelinspect.domain.conclusions import (
    ExperimentConclusion,
    conclusion_from_payload,
    conclusion_payload,
)
from gelinspect.domain.entities import (
    Experiment,
    FeatureValue,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
)
from gelinspect.domain.enums import (
    BatchKind,
    ConclusionVerdict,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentKind,
    ReviewStatus,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.infrastructure.db.models import (
    ExperimentConclusionRow,
    ExperimentRow,
    ReagentBatchRow,
    TubeAssignmentRow,
    TubeResultRow,
)
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    count_where,
    page_of,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    ExperimentQuery,
    ExperimentRepository,
    ExperimentSummary,
    NotFoundError,
    Page,
    PageRequest,
    StateConflictError,
)


def _features_from_json(raw: dict[str, Any]) -> dict[str, FeatureValue]:
    """JSON 中的二元数组还原为元组（质心坐标）。"""
    return {
        key: (float(value[0]), float(value[1])) if isinstance(value, list) else value
        for key, value in raw.items()
    }


def _result_row(experiment_id: str, result: TubeResult) -> TubeResultRow:
    """领域单管结果转 ORM 行。"""
    return TubeResultRow(
        experiment_id=experiment_id,
        tube_no=result.tube_no,
        analysis_status=result.analysis_status.value,
        gel_state=None if result.gel_state is None else result.gel_state.value,
        auto_interpretation=result.auto_interpretation.value,
        review_status=result.review_status.value,
        score=result.score,
        confidence=result.confidence,
        features=dict(result.features),
        model_version=result.model_version,
        config_version=result.config_version,
        elapsed_ms=result.elapsed_ms,
        message=result.message,
        reviewed_interpretation=(
            None if result.reviewed_interpretation is None else result.reviewed_interpretation.value
        ),
        reviewed_by=result.reviewed_by,
        reviewed_at=result.reviewed_at,
        review_note=result.review_note,
    )


def _to_result(row: TubeResultRow) -> TubeResult:
    """ORM 行转领域单管结果。"""
    return TubeResult(
        tube_no=row.tube_no,
        analysis_status=TubeAnalysisStatus(row.analysis_status),
        gel_state=None if row.gel_state is None else GelState(row.gel_state),
        auto_interpretation=Interpretation(row.auto_interpretation),
        review_status=ReviewStatus(row.review_status),
        score=row.score,
        confidence=row.confidence,
        features=_features_from_json(row.features),
        model_version=row.model_version,
        config_version=row.config_version,
        message=row.message,
        reviewed_interpretation=(
            None
            if row.reviewed_interpretation is None
            else Interpretation(row.reviewed_interpretation)
        ),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
        elapsed_ms=row.elapsed_ms,
    )


def _to_assignment(row: TubeAssignmentRow) -> TubeAssignment:
    """ORM 行转领域管位安排。"""
    return TubeAssignment(
        tube_no=row.tube_no,
        sample=Sample(
            sample_id=row.sample_id,
            name=row.sample_name,
            serial_no=row.sample_serial_no,
            batch_no=row.sample_batch_no,
        ),
        role=TubeRole(row.role),
        dilution_factor=row.dilution_factor,
        spiked_endotoxin=row.spiked_endotoxin,
    )


def _to_reagent(row: ReagentBatchRow) -> ReagentBatch:
    """ORM 行转领域试剂批次。"""
    return ReagentBatch(
        kind=ReagentKind(row.kind),
        batch_no=row.batch_no,
        expiry_date=row.expiry_date,
        registered=row.registered,
        labeled_sensitivity=row.labeled_sensitivity,
    )


def _write_conclusion(
    session: Session, experiment_id: str, conclusion: ExperimentConclusion
) -> None:
    """写入结论，覆盖这个试验已有的一行。"""
    if conclusion.computed_at is None:
        raise ValueError("只有已经固化（带计算时间）的结论才能入库")
    session.execute(
        delete(ExperimentConclusionRow).where(
            ExperimentConclusionRow.experiment_id == experiment_id
        )
    )
    session.add(
        ExperimentConclusionRow(
            experiment_id=experiment_id,
            verdict=conclusion.verdict.value,
            value=conclusion.value,
            rule_version=conclusion.rule_version,
            labeled_sensitivity=conclusion.labeled_sensitivity,
            computed_at=conclusion.computed_at,
            payload=conclusion_payload(conclusion),
        )
    )


def _to_conclusion(
    experiment_type: ExperimentType, row: ExperimentConclusionRow
) -> ExperimentConclusion:
    """ORM 行还原结论，不重新计算。"""
    return conclusion_from_payload(
        experiment_type=experiment_type,
        verdict=ConclusionVerdict(row.verdict),
        value=row.value,
        rule_version=row.rule_version,
        labeled_sensitivity=row.labeled_sensitivity,
        computed_at=row.computed_at,
        payload=row.payload,
    )


def _upsert_results(session: Session, experiment_id: str, results: Sequence[TubeResult]) -> None:
    """覆盖写入一批单管结果。"""
    tube_nos = [result.tube_no for result in results]
    if not tube_nos:
        return
    session.execute(
        delete(TubeResultRow).where(
            TubeResultRow.experiment_id == experiment_id, TubeResultRow.tube_no.in_(tube_nos)
        )
    )
    session.add_all(_result_row(experiment_id, result) for result in results)


def _filters(query: ExperimentQuery) -> list[ColumnElement[bool]]:
    """把历史查询条件转成过滤表达式。"""
    filters: list[ColumnElement[bool]] = []
    if query.experiment_type is not None:
        filters.append(ExperimentRow.experiment_type == query.experiment_type.value)
    if query.start is not None:
        filters.append(ExperimentRow.created_at >= query.start)
    if query.end is not None:
        filters.append(ExperimentRow.created_at < query.end)
    if query.state is not None:
        filters.append(ExperimentRow.state == query.state.value)
    if query.operator is not None:
        filters.append(ExperimentRow.operator == query.operator)
    if query.batch_no is not None:
        filters.append(
            or_(
                exists().where(
                    ReagentBatchRow.experiment_id == ExperimentRow.experiment_id,
                    ReagentBatchRow.batch_no == query.batch_no,
                ),
                exists().where(
                    TubeAssignmentRow.experiment_id == ExperimentRow.experiment_id,
                    TubeAssignmentRow.sample_batch_no == query.batch_no,
                ),
            )
        )
    return filters


class SqlExperimentRepository(ExperimentRepository):
    """基于 SQLAlchemy 的试验仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def save(self, experiment: Experiment) -> None:
        """新增或整体覆盖试验（含管位安排、试剂批次和单管结果）。

        在一个事务内完成；已有的子记录先删除再写入。

        Args:
            experiment: 试验聚合。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(ExperimentRow, experiment.experiment_id)
            if row is None:
                row = ExperimentRow(experiment_id=experiment.experiment_id)
                session.add(row)
            row.experiment_type = experiment.experiment_type.value
            row.template_id = experiment.template_id
            row.instrument_no = experiment.instrument_no
            row.operator = experiment.operator
            row.created_at = experiment.created_at
            row.state = experiment.state.value
            row.measured_sensitivity = experiment.measured_sensitivity
            row.mvd = experiment.mvd
            row.endotoxin_limit = experiment.endotoxin_limit
            row.signed_by = experiment.signed_by
            row.signed_at = experiment.signed_at
            row.completed_at = experiment.completed_at
            row.layout_confirmed = experiment.layout_confirmed
            session.flush()
            for table in (TubeAssignmentRow, ReagentBatchRow, TubeResultRow):
                session.execute(delete(table).where(table.experiment_id == row.experiment_id))
            if experiment.conclusion is not None:
                _write_conclusion(session, experiment.experiment_id, experiment.conclusion)
            session.add_all(
                TubeAssignmentRow(
                    experiment_id=experiment.experiment_id,
                    tube_no=a.tube_no,
                    sample_id=a.sample.sample_id,
                    sample_name=a.sample.name,
                    sample_serial_no=a.sample.serial_no,
                    sample_batch_no=a.sample.batch_no,
                    role=a.role.value,
                    dilution_factor=a.dilution_factor,
                    spiked_endotoxin=a.spiked_endotoxin,
                )
                for a in experiment.assignments
            )
            session.add_all(
                ReagentBatchRow(
                    experiment_id=experiment.experiment_id,
                    kind=b.kind.value,
                    batch_no=b.batch_no,
                    expiry_date=b.expiry_date,
                    position=position,
                    registered=b.registered,
                    labeled_sensitivity=b.labeled_sensitivity,
                )
                for position, b in enumerate(experiment.reagents)
            )
            session.add_all(
                _result_row(experiment.experiment_id, result)
                for result in experiment.results.values()
            )

    def save_results(self, experiment_id: str, results: Sequence[TubeResult]) -> None:
        """新增或覆盖若干单管结果，供试验引擎按排写入。

        Args:
            experiment_id: 试验标识。
            results: 单管结果。

        Raises:
            NotFoundError: 试验不存在。
        """
        with translate_lock_errors(), self._sessions() as session:
            if session.get(ExperimentRow, experiment_id) is None:
                raise NotFoundError(f"试验不存在：{experiment_id}")
            _upsert_results(session, experiment_id, results)

    def record_completion(
        self,
        experiment_id: str,
        *,
        completed_at: datetime,
        measured_sensitivity: float | None,
        conclusion: ExperimentConclusion,
    ) -> None:
        """写入完成时间、实测灵敏度和结论，与「已完成」状态在同一事务调用。

        Args:
            experiment_id: 试验标识。
            completed_at: 完成时间。
            measured_sensitivity: 实测灵敏度，结论不是合格或不合格时为 None。
            conclusion: 已固化的结论。

        Raises:
            NotFoundError: 试验不存在。
            ValueError: 结论没有计算时间。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(ExperimentRow, experiment_id)
            if row is None:
                raise NotFoundError(f"试验不存在：{experiment_id}")
            row.completed_at = completed_at
            row.measured_sensitivity = measured_sensitivity
            _write_conclusion(session, experiment_id, conclusion)

    def update_state(
        self,
        experiment_id: str,
        state: ExperimentState,
        *,
        expected: ExperimentState | None = None,
    ) -> None:
        """只更新试验状态，不触碰子记录。

        Args:
            experiment_id: 试验标识。
            state: 新状态。
            expected: 预期的当前状态，不为 None 时做比较后更新。

        Raises:
            NotFoundError: 试验不存在。
            StateConflictError: 当前状态与 ``expected`` 不符。
        """
        statement = update(ExperimentRow).where(ExperimentRow.experiment_id == experiment_id)
        if expected is not None:
            statement = statement.where(ExperimentRow.state == expected.value)
        with translate_lock_errors(), self._sessions() as session:
            result = cast("CursorResult[Any]", session.execute(statement.values(state=state.value)))
            if result.rowcount == 0:
                if session.get(ExperimentRow, experiment_id) is None:
                    raise NotFoundError(f"试验不存在：{experiment_id}")
                raise StateConflictError(
                    f"试验 {experiment_id} 的状态不是预期的 {expected}，可能已被其他操作修改"
                )

    def record_signature(self, experiment_id: str, *, signed_by: str, signed_at: datetime) -> None:
        """写入签署人和签署时间，与「已签署」状态在同一事务调用。

        Args:
            experiment_id: 试验标识。
            signed_by: 签署人用户名。
            signed_at: 签署时间。

        Raises:
            NotFoundError: 试验不存在。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(ExperimentRow, experiment_id)
            if row is None:
                raise NotFoundError(f"试验不存在：{experiment_id}")
            row.signed_by = signed_by
            row.signed_at = signed_at

    def get(self, experiment_id: str) -> Experiment | None:
        """按标识加载完整试验。

        Args:
            experiment_id: 试验标识。

        Returns:
            试验聚合，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(ExperimentRow, experiment_id)
            if row is None:
                return None
            assignments = session.execute(
                select(TubeAssignmentRow)
                .where(TubeAssignmentRow.experiment_id == experiment_id)
                .order_by(TubeAssignmentRow.tube_no)
            ).scalars()
            reagents = session.execute(
                select(ReagentBatchRow)
                .where(ReagentBatchRow.experiment_id == experiment_id)
                .order_by(ReagentBatchRow.position)
            ).scalars()
            results = session.execute(
                select(TubeResultRow).where(TubeResultRow.experiment_id == experiment_id)
            ).scalars()
            experiment_type = ExperimentType(row.experiment_type)
            conclusion = session.get(ExperimentConclusionRow, experiment_id)
            return Experiment(
                experiment_id=row.experiment_id,
                experiment_type=experiment_type,
                template_id=row.template_id,
                instrument_no=row.instrument_no,
                operator=row.operator,
                created_at=row.created_at,
                assignments=tuple(_to_assignment(a) for a in assignments),
                reagents=tuple(_to_reagent(r) for r in reagents),
                state=ExperimentState(row.state),
                results={r.tube_no: _to_result(r) for r in results},
                measured_sensitivity=row.measured_sensitivity,
                mvd=row.mvd,
                endotoxin_limit=row.endotoxin_limit,
                signed_by=row.signed_by,
                signed_at=row.signed_at,
                layout_confirmed=row.layout_confirmed,
                completed_at=row.completed_at,
                conclusion=(
                    None if conclusion is None else _to_conclusion(experiment_type, conclusion)
                ),
            )

    def query(self, query: ExperimentQuery, page: PageRequest) -> Page[ExperimentSummary]:
        """分页查询历史试验，按创建时间倒序。

        Args:
            query: 查询条件。
            page: 分页请求。

        Returns:
            一页试验摘要。
        """
        filters = _filters(query)
        tube_count = (
            select(func.count())
            .where(TubeAssignmentRow.experiment_id == ExperimentRow.experiment_id)
            .scalar_subquery()
        )
        statement = page_of(
            select(ExperimentRow, tube_count)
            .where(*filters)
            .order_by(ExperimentRow.created_at.desc(), ExperimentRow.experiment_id.desc()),
            page,
        )
        with self._sessions() as session:
            total = count_where(session, ExperimentRow, filters)
            items = tuple(
                ExperimentSummary(
                    experiment_id=row.experiment_id,
                    experiment_type=ExperimentType(row.experiment_type),
                    template_id=row.template_id,
                    instrument_no=row.instrument_no,
                    operator=row.operator,
                    created_at=row.created_at,
                    state=ExperimentState(row.state),
                    tube_count=int(count),
                    signed_by=row.signed_by,
                    signed_at=row.signed_at,
                    completed_at=row.completed_at,
                )
                for row, count in session.execute(statement)
            )
        return Page(items=items, total=total, request=page)

    def _batch_filter(self, kind: BatchKind, batch_no: str | None) -> ColumnElement[bool]:
        """某种批号的存在性条件（``batch_no`` 为 None 时不限定批号，只要有批号）。"""
        if kind is BatchKind.LAL_REAGENT:
            conditions: list[ColumnElement[bool]] = [
                ReagentBatchRow.experiment_id == ExperimentRow.experiment_id,
                ReagentBatchRow.kind == ReagentKind.LAL_REAGENT.value,
            ]
            if batch_no is not None:
                conditions.append(ReagentBatchRow.batch_no == batch_no)
            return exists().where(*conditions)
        conditions = [
            TubeAssignmentRow.experiment_id == ExperimentRow.experiment_id,
            TubeAssignmentRow.sample_batch_no.is_not(None),
            TubeAssignmentRow.sample_batch_no != "",
        ]
        if batch_no is not None:
            conditions.append(TubeAssignmentRow.sample_batch_no == batch_no)
        return exists().where(*conditions)

    def batch_numbers(self, kind: BatchKind, states: Sequence[ExperimentState]) -> list[str]:
        """处于给定状态的试验用到的批号（去重，升序）。

        Args:
            kind: 批号种类。
            states: 试验状态。

        Returns:
            批号列表。
        """
        values = [state.value for state in states]
        with self._sessions() as session:
            if kind is BatchKind.LAL_REAGENT:
                lal = session.execute(
                    select(ReagentBatchRow.batch_no)
                    .join(
                        ExperimentRow, ExperimentRow.experiment_id == ReagentBatchRow.experiment_id
                    )
                    .where(
                        ReagentBatchRow.kind == ReagentKind.LAL_REAGENT.value,
                        ExperimentRow.state.in_(values),
                    )
                    .distinct()
                ).scalars()
                return sorted({str(no) for no in lal})
            sample = session.execute(
                select(TubeAssignmentRow.sample_batch_no)
                .join(ExperimentRow, ExperimentRow.experiment_id == TubeAssignmentRow.experiment_id)
                .where(
                    TubeAssignmentRow.sample_batch_no.is_not(None),
                    TubeAssignmentRow.sample_batch_no != "",
                    ExperimentRow.state.in_(values),
                )
                .distinct()
            ).scalars()
            return sorted({str(no) for no in sample})

    def experiment_ids_for_batch(
        self, kind: BatchKind, batch_no: str, states: Sequence[ExperimentState]
    ) -> list[str]:
        """用到某批号、且处于给定状态的试验标识，按创建时间升序。

        Args:
            kind: 批号种类。
            batch_no: 批号。
            states: 试验状态。

        Returns:
            试验标识列表。
        """
        statement = (
            select(ExperimentRow.experiment_id)
            .where(
                ExperimentRow.state.in_([state.value for state in states]),
                self._batch_filter(kind, batch_no),
            )
            .order_by(ExperimentRow.created_at, ExperimentRow.experiment_id)
        )
        with self._sessions() as session:
            return list(session.execute(statement).scalars())
