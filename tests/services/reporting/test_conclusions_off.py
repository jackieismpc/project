"""试验结论开关（``conclusions.enabled``，默认关闭）：关闭时不计算、不入库、不显示结论，签署不要求结论。

判定规则甲方尚未确认，商用软件不能显示未确认的结论。这里同时验证打开时的行为没有变。
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest
import yaml
from openpyxl import load_workbook

from gelinspect.domain.conclusions import finalize_completion
from gelinspect.domain.entities import TubeResult
from gelinspect.domain.enums import AuditAction, BatchKind, GelState, TubeAnalysisStatus
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.signing import SigningBlocker, signing_blocks
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.services.config import DEFAULT_CONFIG, parse_config
from gelinspect.services.export_service import ExportService
from gelinspect.services.reporting.renderer import ReportRenderer
from gelinspect.services.reporting.report_service import SigningNotAllowedError
from gelinspect.services.reporting.texts import ReportLanguage, ReportTexts
from simulation.sample_data import SENSITIVITY_STATES, sensitivity_experiment
from tests.services.conftest import NOW, PASSWORDS, Stack

from .conftest import make_completed, make_service
from .pdftext import flat_text

ROOT = Path(__file__).resolve().parents[3]

CONCLUSION_WORDS_ZH = ("λc", "合格", "无效", "待定", "依据", "规则版本", "实测灵敏度", "结论")
CONCLUSION_WORDS_EN = (
    "λc",
    "Conclusion",
    "Qualified",
    "Invalid",
    "Pending",
    "Basis",
    "Rule version",
    "Measured sensitivity",
)


def _assert_none_of(text: str, words: tuple[str, ...]) -> None:
    found = [word for word in words if word in text]
    assert not found, f"不应出现结论文字：{found}"


# ---------------------------------------------------------------- 配置


def test_conclusions_are_off_by_default_in_every_default_config() -> None:
    assert parse_config(DEFAULT_CONFIG).conclusions.enabled is False
    assert DEFAULT_CONFIG["conclusions"] == {"enabled": False}
    release = yaml.safe_load((ROOT / "packaging/config/release.yaml").read_text(encoding="utf-8"))
    assert release["conclusions"] == {"enabled": False}
    example = yaml.safe_load((ROOT / "simulation/config.example.yaml").read_text(encoding="utf-8"))
    assert example["conclusions"] == {"enabled": False}
    # 配置里没有这一段时同样关闭；写 true 才打开
    bare = {k: v for k, v in DEFAULT_CONFIG.items() if k != "conclusions"}
    assert parse_config(bare).conclusions.enabled is False
    assert parse_config({**bare, "conclusions": {"enabled": True}}).conclusions.enabled is True


# ---------------------------------------------------------------- 完成时不计算、不入库


def test_completion_does_not_compute_or_store_a_conclusion_when_off(signing_stack: Stack) -> None:
    stack = signing_stack
    stored = make_completed(stack, conclusions_enabled=False)
    assert stored.state is ExperimentState.COMPLETED
    assert stored.conclusion is None and stored.measured_sensitivity is None
    assert stored.completed_at is not None  # 完成时间仍然记录
    assert stack.audit(AuditAction.CONCLUSION_RECORDED) == []


def test_completion_still_records_a_conclusion_when_on(signing_stack: Stack) -> None:
    stack = signing_stack
    stored = make_completed(stack, conclusions_enabled=True)
    assert stored.conclusion is not None and stored.conclusion.value is not None
    assert stored.measured_sensitivity == stored.conclusion.value
    assert len(stack.audit(AuditAction.CONCLUSION_RECORDED)) == 1


def test_finalize_completion_off_only_sets_the_completion_time(signing_stack: Stack) -> None:
    stored = make_completed(signing_stack, conclusions_enabled=True)
    assert stored.conclusion is not None
    # 关闭时即使试验上带着旧结论，也不会再被当作这次完成的结论
    assert finalize_completion(stored, NOW, conclusions_enabled=False) is None
    assert stored.conclusion is None and stored.measured_sensitivity is None
    assert stored.completed_at == NOW
    with pytest.raises(ValueError, match="时区"):
        finalize_completion(stored, NOW.replace(tzinfo=None), conclusions_enabled=False)


# ---------------------------------------------------------------- 签署


def test_signing_needs_no_conclusion_when_off_but_still_needs_review_and_completion(
    signing_stack: Stack,
) -> None:
    stack = signing_stack
    # 打开时：布局未确认 → 待定，不能签署；阴性对照阳性 → 无效，不能签署；没有入库结论不能签署
    pending = make_completed(stack, layout_confirmed=False, conclusions_enabled=True)
    assert [b.blocker for b in signing_blocks(pending, conclusions_enabled=True)] == [
        SigningBlocker.VERDICT_PENDING
    ]
    assert signing_blocks(pending, conclusions_enabled=False) == ()
    clean = make_completed(
        stack, experiment_id="EXP-NOCON", states=dict(SENSITIVITY_STATES), conclusions_enabled=False
    )
    assert [b.blocker for b in signing_blocks(clean, conclusions_enabled=True)] == [
        SigningBlocker.NO_CONCLUSION
    ]
    assert signing_blocks(clean, conclusions_enabled=False) == ()
    # 关闭时仍然要求：试验已完成、全部待复核已处理
    clean.state = ExperimentState.PENDING_REVIEW
    assert [b.blocker for b in signing_blocks(clean, conclusions_enabled=False)] == [
        SigningBlocker.PENDING_REVIEW
    ]
    clean.state = ExperimentState.INCUBATING
    assert [b.blocker for b in signing_blocks(clean, conclusions_enabled=False)] == [
        SigningBlocker.NOT_COMPLETED
    ]


def test_can_sign_when_off_even_if_the_rules_would_say_invalid_or_pending(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    states = {**SENSITIVITY_STATES, 17: GelState.FULLY_GELLED}  # 打开时：阴性对照阳性 → 无效
    make_completed(stack, states=states, conclusions_enabled=False)
    on_service, _ = make_service(stack, tmp_path, conclusions_enabled=True)
    with pytest.raises(SigningNotAllowedError):  # 同一个试验，打开时没有入库结论，不能签署
        on_service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])

    service, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    assert preview.can_sign and preview.conclusion is None and not preview.conclusions_enabled
    result = service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.SIGNED
    assert stored.conclusion is None
    _assert_none_of(flat_text(result.path.read_bytes()), CONCLUSION_WORDS_ZH)
    # 审计里也没有任何结论内容
    signed = stack.audit(AuditAction.EXPERIMENT_SIGNED)
    assert len(signed) == 1 and "结论" not in signed[0] and "语言" in signed[0]
    assert stack.audit(AuditAction.CONCLUSION_RECORDED) == []


def test_signing_still_refuses_unreviewed_tubes_when_off(
    signing_stack: Stack, tmp_path: Path
) -> None:
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
    service, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    with pytest.raises(SigningNotAllowedError) as caught:
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert [b.blocker for b in caught.value.blocks] == [SigningBlocker.PENDING_REVIEW]


def test_turning_the_switch_on_later_does_not_invent_conclusions(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=False)
    on_service, _ = make_service(stack, tmp_path, conclusions_enabled=True)
    preview = on_service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    # 关闭期间完成的试验没有入库结论：不按当前规则补算，打开后也不能签署
    assert [b.blocker for b in preview.blocks] == [SigningBlocker.NO_CONCLUSION]


def test_turning_the_switch_off_hides_a_conclusion_recorded_earlier(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=True)
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.conclusion is not None
    service, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    assert preview.conclusion is None and preview.document.sections[0].conclusion is None
    assert preview.can_sign


# ---------------------------------------------------------------- PDF


def test_pdf_has_no_conclusion_text_in_chinese_and_english_when_off(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=False)
    service, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    renderer = ReportRenderer(zone=UTC)
    zh, _ = renderer.render_pdf(preview.document, ReportTexts(ReportLanguage.ZH_CN))
    text = flat_text(zh)
    _assert_none_of(text, CONCLUSION_WORDS_ZH)
    # 其余内容仍在：结果表、阴阳性、复核标注
    for expected in ("试管编号", "阳性（已复核）", "阴性", "标注“已复核”的结果"):
        assert expected in text, expected
    en, _ = renderer.render_pdf(preview.document, ReportTexts(ReportLanguage.EN))
    _assert_none_of(flat_text(en), CONCLUSION_WORDS_EN)


def test_pdf_still_has_the_conclusion_when_on(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=True)
    service, _ = make_service(stack, tmp_path, conclusions_enabled=True)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    data, _ = ReportRenderer(zone=UTC).render_pdf(
        preview.document, ReportTexts(ReportLanguage.ZH_CN)
    )
    text = flat_text(data)
    for expected in ("结论：合格", "λc = 0.5946", "sensitivity-v1", "规则版本"):
        assert expected in text, expected


def test_content_digest_excludes_the_conclusion_when_off(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=True)
    off, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    on, _ = make_service(stack, tmp_path, conclusions_enabled=True)
    session = stack.login("analyst")
    assert (
        off.preview(session, "EXP-SAMPLE-0001").digest
        != on.preview(session, "EXP-SAMPLE-0001").digest
    )


def test_batch_report_has_no_conclusion_column_when_off(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=False)
    service, _ = make_service(stack, tmp_path, conclusions_enabled=False)
    service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    preview = service.batch_preview(stack.login("analyst"), BatchKind.LAL_REAGENT, "LAL2606A")
    renderer = ReportRenderer(zone=UTC)
    for language in (ReportLanguage.ZH_CN, ReportLanguage.EN):
        data, _ = renderer.render_pdf(preview.document, ReportTexts(language))
        text = flat_text(data)
        _assert_none_of(
            text, CONCLUSION_WORDS_ZH if language is ReportLanguage.ZH_CN else CONCLUSION_WORDS_EN
        )
        assert "EXP-SAMPLE-0001" in text


# ---------------------------------------------------------------- Excel


def test_excel_has_no_conclusion_columns_or_sheet_when_off(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=True)  # 即使库里有旧结论，关闭时也不导出
    exports = ExportService(stack.auth, stack.database, clock=lambda: NOW)
    target = tmp_path / "off.xlsx"
    exports.export_results(stack.login("analyst"), ["EXP-SAMPLE-0001"], target)
    book = load_workbook(target)
    assert book.sheetnames == ["试验", "单管结果"]
    for sheet in book:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    _assert_none_of(
                        cell.value, ("λc", "合格", "无效", "待定", "依据", "规则版本", "结论")
                    )
    head = [c.value for c in book["试验"][1]]
    assert head[-2:] == ["管数", "待复核管数"] and "MVD" in head
    tubes = [c.value for c in book["单管结果"][1]]
    assert "最终判读" in tubes and "复核状态" in tubes  # 阴阳性和复核仍在


def test_excel_still_has_the_conclusion_when_on(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack, conclusions_enabled=True)
    exports = ExportService(stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=True)
    target = tmp_path / "on.xlsx"
    exports.export_results(stack.login("analyst"), ["EXP-SAMPLE-0001"], target)
    book = load_workbook(target)
    assert book.sheetnames == ["试验", "单管结果", "结论依据"]
    head = [c.value for c in book["试验"][1]]
    for needed in ("实测灵敏度 λc（EU/mL）", "结论", "结论数值", "规则版本"):
        assert needed in head
