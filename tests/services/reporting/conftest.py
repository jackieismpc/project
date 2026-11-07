"""报告测试的共用夹具：真实 SQLite、真实 Qt 排版和 pyHanko 签名。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gelinspect.bootstrap import Argon2Hasher  # noqa: E402
from gelinspect.domain.entities import Experiment, TubeResult, User  # noqa: E402
from gelinspect.domain.enums import (  # noqa: E402
    GelState,
    Interpretation,
    Role,
    TubeAnalysisStatus,
)
from gelinspect.domain.interpretation import GelStateMapping  # noqa: E402
from gelinspect.domain.state_machine import ExperimentEvent  # noqa: E402
from gelinspect.services.reporting.keystore import LocalKeyStore  # noqa: E402
from gelinspect.services.reporting.report_service import ReportService  # noqa: E402
from gelinspect.services.review_service import ReviewService  # noqa: E402
from simulation.sample_data import SENSITIVITY_STATES, sensitivity_experiment  # noqa: E402
from tests.services.conftest import NOW, Stack, build_stack  # noqa: E402

SIGN_TIME = datetime(2026, 11, 5, 13, 30, tzinfo=UTC)
"""测试里的签署时间（与 ``NOW`` 不同，方便区分）。"""


@pytest.fixture(autouse=True)
def gui_app(qapp: QApplication) -> QApplication:
    """报告排版需要 ``QGuiApplication``：沿用 pytest-qt 的应用，避免和界面测试抢着创建。"""
    return qapp


def add_user(stack: Stack, username: str, display_name: str, role: Role) -> None:
    """加一个用户（密码与用户名相同，只在需要时登录）。"""
    stack.database.users.add(
        User(user_id=f"u-{username}", username=username, display_name=display_name, role=role),
        password_hash=Argon2Hasher().hash(username + "-Pass1"),
        must_change_password=False,
    )


def make_completed(
    stack: Stack,
    *,
    experiment_id: str = "EXP-SAMPLE-0001",
    states: dict[int, GelState] | None = None,
    review_to: Interpretation = Interpretation.POSITIVE,
    layout_confirmed: bool = True,
    operator: str = "operator01",
    sample_batch: str = "SB2606",
    conclusions_enabled: bool = True,
) -> Experiment:
    """造一个已完成的灵敏度复核试验：先写入分析结果，再由分析员复核全部待复核的管。

    ``conclusions_enabled`` 为 False 时模拟结论功能关闭：完成时不计算、不入库结论。
    """
    experiment = sensitivity_experiment()
    experiment.experiment_id = experiment_id
    experiment.layout_confirmed = layout_confirmed
    experiment.operator = operator
    experiment.assignments = tuple(
        type(a)(
            tube_no=a.tube_no,
            sample=type(a.sample)(
                sample_id=a.sample.sample_id,
                name=a.sample.name,
                serial_no=a.sample.serial_no,
                batch_no=sample_batch,
            ),
            role=a.role,
            dilution_factor=a.dilution_factor,
            spiked_endotoxin=a.spiked_endotoxin,
        )
        for a in experiment.assignments
    )
    for event in (
        ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK,
        ExperimentEvent.REAGENT_CHECK_PASSED,
        ExperimentEvent.START,
        ExperimentEvent.ROW_DUE,
        ExperimentEvent.ROW_CAPTURED,
    ):
        experiment.apply(event)
    for no, state in (states or dict(SENSITIVITY_STATES)).items():
        experiment.record_result(
            TubeResult.from_analysis(
                tube_no=no, status=TubeAnalysisStatus.OK, state=state, mapping=GelStateMapping()
            )
        )
    if experiment.pending_review_tubes:
        experiment.apply(ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW)
        stack.database.experiments.save(experiment)
        service = ReviewService(
            stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=conclusions_enabled
        )
        for tube in experiment.pending_review_tubes:
            service.review_tube(stack.login("analyst"), experiment_id, tube, review_to, "复核")
    else:
        # 没有待复核的管：引擎直接完成并固化结论（与引擎的做法一致）。
        from gelinspect.domain.conclusions import finalize_completion

        experiment.apply(ExperimentEvent.ALL_ANALYZED_CLEAN)
        finalize_completion(experiment, NOW, conclusions_enabled=conclusions_enabled)
        stack.database.experiments.save(experiment)
    stored = stack.database.experiments.get(experiment_id)
    assert stored is not None
    return stored


def make_service(
    stack: Stack, tmp_path: Path, **overrides: object
) -> tuple[ReportService, LocalKeyStore]:
    """装配报告服务：证书放在 ``tmp_path/data/keys``，报告放在 ``tmp_path/data/reports``。"""
    data = tmp_path / "data"
    keystore = LocalKeyStore(data / "keys", common_name="GelInspect Test Signing",
                             organization="测试机构")  # fmt: skip
    options: dict[str, object] = {
        "data_dir": data,
        "keystore": keystore,
        "clock": lambda: SIGN_TIME,
        "zone": UTC,
        "conclusions_enabled": True,  # 这里的测试都在结论功能打开的条件下做；关闭的用例显式传 False
    }
    options.update(overrides)
    service = ReportService(stack.auth, stack.database, **options)  # type: ignore[arg-type]
    return service, keystore


@pytest.fixture
def signing_stack(tmp_path: Path) -> Iterator[Stack]:
    """真实 SQLite；分析员显示名改成中文“李工”，另有中文操作人“张工”。"""
    stack = build_stack(tmp_path)
    analyst = stack.database.users.get_by_username("analyst")
    assert analyst is not None
    stack.database.users.update(
        User(analyst.user_id, analyst.username, "李工", analyst.role, analyst.active)
    )
    add_user(stack, "zhang", "张工", Role.EXPERIMENTER)
    yield stack
    stack.database.close()
