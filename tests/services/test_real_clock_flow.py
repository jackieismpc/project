"""真实时钟下的全流程：试验开始 ≤ 完成 ≤ 签署，报告显示的就是这些值。

背景：P11 自检脚本给服务注入了固定时钟（``clock=lambda: FIXED_NOW``），而引擎一直用真实系统时钟，
所以报告上「检测日期」（取自引擎写的开始事件，真实时间）晚于「签署日期」（取自服务时钟，固定）。
这是注入造成的，不是真实路径的问题：这里不注入任何时钟，三处时间来源一致。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from gelinspect.app_context import build_app_context
from gelinspect.domain.enums import AuditAction, Interpretation, ReagentKind
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.config import DEFAULT_CONFIG, parse_config
from gelinspect.services.experiment_draft import ExperimentDraft, NewExperimentRequest
from gelinspect.services.reporting.report_service import ReportService
from tests.services.reporting.pdftext import flat_text

from .conftest import PASSWORDS
from .engine_helpers import eventually
from .test_new_experiment_engine import SENSITIVITY

FAR_CODES = {
    ReagentKind.LAL_REAGENT: "GL-LAL-0.25|LAL2606A|lal_reagent|20991231|0001",
    ReagentKind.ENDOTOXIN: "GL-CSE-10|CSE2605A|endotoxin|20991231|0001",
    ReagentKind.WATER: "GL-BET-W|WAT2607A|water|20991231|0001",
}
"""有效期写到 2099 年的试剂码：测试不随真实日期过期（码里的有效期优先于备案清单）。"""

STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def test_start_complete_and_sign_are_ordered_and_the_report_shows_them(
    qapp: QApplication, tmp_path: Path
) -> None:
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    config["data_dir"] = str(tmp_path / "data")
    config["engine"] = {"incubation_minutes": 0.05, "time_scale": 60, "row_late_warn_s": 1e6}
    config["video"] = {
        "source": "simulation.sim_video:SimVideoSource",
        "device": "sim",
        "width": 640,
        "height": 360,
        "fps": 30,
    }
    context = build_app_context(parse_config(config))  # 没有注入任何时钟
    try:
        before = datetime.now(UTC)
        experimenter = context.auth.login("experimenter", PASSWORDS["experimenter"])
        draft = ExperimentDraft()
        draft.choose_template(SENSITIVITY)
        draft.set_selected(range(1, 18))
        base = draft.to_request()
        experiment_id = context.experiments.create_experiment(
            experimenter, NewExperimentRequest(base.template_id, base.assignments, FAR_CODES)
        )
        context.experiments.start_experiment(experimenter, experiment_id)

        def finished() -> bool:
            found = context.database.experiments.get(experiment_id)
            return found is not None and found.state in (
                ExperimentState.PENDING_REVIEW,
                ExperimentState.COMPLETED,
            )

        assert eventually(finished, 60)
        assert context.engine.wait(30)
        analyst = context.auth.login("analyst", PASSWORDS["analyst"])
        for tube in context.database.experiments.get(experiment_id).pending_review_tubes:  # type: ignore[union-attr]
            context.reviews.review_tube(
                analyst, experiment_id, tube, Interpretation.POSITIVE, "复核"
            )
        reports: ReportService = context.reports
        signed = reports.sign(analyst, experiment_id, PASSWORDS["analyst"])
        after = datetime.now(UTC)

        stored = context.database.experiments.get(experiment_id)
        assert stored is not None and stored.state is ExperimentState.SIGNED
        assert stored.completed_at is not None and stored.signed_at is not None
        started_events = context.database.audit.query(
            AuditQuery(experiment_id=experiment_id, action=AuditAction.EXPERIMENT_STATE_CHANGED),
            PageRequest(limit=1000),
        )
        started = min(
            r.event.occurred_at
            for r in started_events.items
            if "ready → incubating" in r.event.detail
        )
        # 同一个时间轴：创建 ≤ 开始 ≤ 完成 ≤ 签署，都落在这次测试的真实时间窗口里
        assert before <= stored.created_at <= started <= stored.completed_at
        assert stored.completed_at <= stored.signed_at <= after
        assert experiment_id == f"EXP-{stored.created_at.astimezone():%Y%m%d-%H%M%S}"

        # 报告显示的是这些值：检测日期 = 试验开始时间，签署日期 = 签署时间，不是报告生成时间
        section = reports.preview(analyst, experiment_id).document.sections[0]
        assert section.started_at == started and section.signed_at == stored.signed_at
        assert section.started_at < stored.signed_at
        text = flat_text(signed.path.read_bytes())
        shown = f"{started.astimezone():%Y-%m-%d %H:%M}"
        signed_shown = f"{stored.signed_at.astimezone():%Y-%m-%d %H:%M}"
        assert shown in text and signed_shown in text
        stamps = STAMP.findall(text)
        assert stamps and min(stamps) == shown <= max(stamps) == signed_shown
    finally:
        context.close()
