"""历史、审计、用户、设置页测试的公共造数据和操作。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, ExperimentType, Role
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.ui.widgets.data_table import DataTable
from tests.infrastructure.db.conftest import make_experiment
from tests.ui.conftest import settle

BASE = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
TYPES = list(ExperimentType)
STATES = [ExperimentState.SIGNED, ExperimentState.COMPLETED, ExperimentState.STOPPED]
OPERATORS = ["experimenter", "admin"]


def seed_experiments(context: AppContext, count: int = 12) -> None:
    """造 ``count`` 个类型、状态、操作人、批号各不相同的试验，创建时间每天一个。"""
    for n in range(count):
        experiment = make_experiment(
            f"EXP-H-{n:03d}",
            experiment_type=TYPES[n % len(TYPES)],
            created_at=BASE + timedelta(days=n),
            tube_count=4,
            batch_no=f"LAL-{n % 3}",
            sample_batch_no=f"SB-{n % 4}",
        )
        experiment.operator = OPERATORS[n % 2]
        experiment.state = STATES[n % 3]
        context.database.experiments.save(experiment)


def seed_audit(context: AppContext, count: int = 130) -> None:
    """造 ``count`` 条审计，操作者 alice 和 bob，每小时一条。"""
    context.database.audit.append_many(
        [
            AuditEvent(
                occurred_at=BASE + timedelta(hours=i),
                actor="alice" if i % 3 else "bob",
                actor_role=Role.ANALYST,
                action=AuditAction.LOGIN if i % 2 else AuditAction.TEMPLATE_CHANGED,
                detail=f"第 {i} 条 批号 LAL-{i % 7}",
                experiment_id=f"E{i % 4}",
            )
            for i in range(count)
        ]
    )


def select_rows(table: DataTable, rows: list[int]) -> None:
    """用选择模型选中若干行（多选模式）。"""
    selection = table.view.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(
            table.model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    settle(QApplication.instance())  # type: ignore[arg-type]


def column_values(table: DataTable, key: str) -> list[object]:
    """表格某一列的全部显示值。"""
    return [row.get(key) for row in table.model.rows]
