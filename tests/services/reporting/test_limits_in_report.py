"""向导里录入的 MVD 和内毒素限值：固化到试验记录，显示在签署后的 PDF 报告里。"""

from __future__ import annotations

from pathlib import Path

from gelinspect.services.reporting.texts import ReportLanguage
from tests.services.conftest import PASSWORDS, Stack

from .conftest import make_completed, make_service
from .pdftext import flat_text


def test_entered_mvd_and_limit_appear_in_the_signed_pdf(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, operator="zhang")
    stack.experiments.update_limits(
        stack.login("experimenter"), "EXP-SAMPLE-0001", mvd=1280.0, endotoxin_limit=0.25
    )
    service, _ = make_service(stack, tmp_path)
    result = service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    text = flat_text(result.path.read_bytes())
    assert "MVD" in text and "1280" in text
    assert "内毒素限值" in text and "0.25 EU/mL" in text


def test_the_english_report_shows_the_entered_limits_too(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, experiment_id="EXP-SAMPLE-0002", operator="zhang")
    stack.experiments.update_limits(
        stack.login("experimenter"), "EXP-SAMPLE-0002", mvd=64.0, endotoxin_limit=10.0
    )
    service, _ = make_service(stack, tmp_path)
    result = service.sign(
        stack.login("analyst"), "EXP-SAMPLE-0002", PASSWORDS["analyst"], ReportLanguage.EN
    )
    text = flat_text(result.path.read_bytes())
    assert "Endotoxin limit" in text and "10 EU/mL" in text and "64" in text
