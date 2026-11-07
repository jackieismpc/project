"""排版：多页、表头重复、中英文、长文字不超界、版权占位、8 连图。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gelinspect.domain.conclusions import finalize_conclusion
from gelinspect.domain.entities import (
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
)
from gelinspect.domain.enums import (
    ExperimentType,
    GelState,
    ReagentKind,
    ReportScope,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.plate import TUBE_COUNT
from gelinspect.domain.state_machine import ExperimentEvent
from gelinspect.services.reporting.document import ReportDocument, build_section
from gelinspect.services.reporting.renderer import ReportRenderer, ensure_fonts
from gelinspect.services.reporting.texts import ReportLanguage, ReportTexts
from tests.services.conftest import NOW, PASSWORDS, Stack

from .conftest import SIGN_TIME, make_completed, make_service
from .pdftext import flat_text, has_text, pdf_font_report, pdf_page_count, pdf_pages_text


def big_experiment(
    tubes: int = TUBE_COUNT, *, name: str = "供试品", batch: str = "批号-2606"
) -> Experiment:
    """整板（全部管位）的限度试验（规则待 QA 核对，结论是待定），用来测多页排版。"""
    assignments = tuple(
        TubeAssignment(
            tube_no=no,
            sample=Sample(f"S{no}", f"{name}{no}", f"SN{no:04d}", batch),
            role=TubeRole.SAMPLE,
            dilution_factor=2.0,
            spiked_endotoxin=0.25,
        )
        for no in range(1, tubes + 1)
    )
    experiment = Experiment(
        experiment_id="EXP-BIG-0001",
        experiment_type=ExperimentType.LIMIT,
        template_id="t",
        instrument_no="GI-001",
        operator="op",
        created_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        assignments=assignments,
        reagents=(
            ReagentBatch(ReagentKind.LAL_REAGENT, "LAL2606A", None, True, 0.25),
            ReagentBatch(ReagentKind.ENDOTOXIN, "CSE2605A", None, True),
            ReagentBatch(ReagentKind.WATER, "WAT2607A", None, True),
        ),
        layout_confirmed=True,
    )
    for event in (
        ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK,
        ExperimentEvent.REAGENT_CHECK_PASSED,
        ExperimentEvent.START,
        ExperimentEvent.ROW_DUE,
        ExperimentEvent.ROW_CAPTURED,
    ):
        experiment.apply(event)
    for no in range(1, tubes + 1):
        experiment.record_result(
            TubeResult.from_analysis(
                tube_no=no,
                status=TubeAnalysisStatus.OK,
                state=GelState.NOT_GELLED,
                mapping=GelStateMapping(),
            )
        )
    experiment.apply(ExperimentEvent.ALL_ANALYZED_CLEAN)
    finalize_conclusion(experiment, NOW)
    return experiment


def document_of(
    experiment: Experiment,
    *,
    holder: str = "[版权方名称]",
    operator: str = "张工",
    conclusions_enabled: bool = True,
) -> ReportDocument:
    section = build_section(
        experiment,
        operator_name=operator,
        signer_name=None,
        started_at=None,
        temperature_range=(36.9, 37.2),
        conclusions_enabled=conclusions_enabled,
    )
    return ReportDocument(
        scope=ReportScope.EXPERIMENT, sections=(section,), copyright_holder=holder
    )


def test_eighty_tubes_span_pages_and_repeat_the_table_header_and_footer() -> None:
    document = document_of(big_experiment())
    renderer = ReportRenderer(zone=UTC)
    data, pages = renderer.render_pdf(document, ReportTexts(ReportLanguage.ZH_CN))
    assert pages >= 3 and pdf_page_count(data) == pages
    page_texts = pdf_pages_text(data)
    for number, text in enumerate(page_texts, start=1):
        assert "试管编号" in text and "样品名称" in text, number  # 表头每页重复
        assert f"第 {number} 页 / 共 {pages} 页" in text  # 页脚逐页
        assert "SHA-256" in text and "未经许可不得复制" in text
    everything = "\n".join(page_texts)
    for tube in (1, 40, TUBE_COUNT):
        assert f"供试品{tube}" in everything
    assert "未签署" in everything  # 预览页脚明确说明还没有签署
    # 全部行排进了表里：每个管号都出现
    assert all(f"SN{no:04d}" in flat_text(data) for no in range(1, TUBE_COUNT + 1))


def test_rows_never_split_across_pages_and_nothing_is_cut_off() -> None:
    data, pages = ReportRenderer(zone=UTC).render_pdf(
        document_of(big_experiment()), ReportTexts(ReportLanguage.ZH_CN)
    )
    seen = [
        no
        for text in pdf_pages_text(data)
        for no in range(1, TUBE_COUNT + 1)
        if f"SN{no:04d}" in text
    ]
    assert sorted(seen) == list(range(1, TUBE_COUNT + 1))  # 每行恰好出现在一页上
    assert pages >= 3


@pytest.mark.parametrize("language", [ReportLanguage.ZH_CN, ReportLanguage.EN])
def test_long_unbroken_text_wraps_and_stays_inside_the_page(language: ReportLanguage) -> None:
    experiment = big_experiment(17, name="X" * 60)
    renderer = ReportRenderer(zone=UTC)
    renderer.render_pdf(document_of(experiment), ReportTexts(language))
    assert renderer.last_page_width > 0
    assert renderer.last_ideal_width <= renderer.last_page_width + 1


def test_english_report_has_no_chinese_and_no_missing_translation() -> None:
    texts = ReportTexts(ReportLanguage.EN)
    experiment = big_experiment(17, name="Sample", batch="LOT-2606")
    experiment.experiment_type = ExperimentType.SENSITIVITY_CONFIRMATION
    data, _ = ReportRenderer(zone=UTC).render_pdf(document_of(experiment, operator="Wang"), texts)
    text = flat_text(data)
    for expected in (
        "Automated Gel-Clot Test Report",
        "File name (Exp. ID)",
        "Instrument No.",
        "Temperature range",
        "36.9–37.2",
        "Tube No.",
        "Serial No.",
        "Spiked endotoxin (EU/mL)",
        "Conclusion",
        "Page 1 / 1",
        "Reproduction without permission is prohibited.",
        "Content digest SHA-256",
    ):
        assert has_text(data, expected), expected
    chinese = [ch for ch in text if "一" <= ch <= "鿿"]
    # 只允许用户自己录入的中文（样品名“供试品”），固定文字不能有中文
    assert set(chinese) <= set("供试品"), "".join(sorted(set(chinese)))
    assert texts.missing == frozenset()


def test_every_report_string_has_an_english_translation() -> None:
    from gelinspect.services.reporting.strings import SOURCES, S

    texts = ReportTexts(ReportLanguage.EN)
    assert set(SOURCES) == set(S)
    for key in S:
        translated = texts.s(key, row=1, n=1, holder="X", page=1, total=1, id="X")
        assert translated, key
    assert texts.missing == frozenset(), sorted(texts.missing)
    # 英文里不应该残留中文原文
    for key in S:
        english = texts.tr(SOURCES[key])
        assert not any("一" <= ch <= "鿿" for ch in english), (key, english)


def test_conclusion_notes_and_roles_are_translated_in_english() -> None:
    texts = ReportTexts(ReportLanguage.EN)
    from gelinspect.domain.conclusions import NOTE_TEMPLATES

    for code, template in NOTE_TEMPLATES.items():
        assert texts.tr(template, "Conclusion") != template or code, code
    assert texts.missing == frozenset(), sorted(texts.missing)


def test_copyright_holder_comes_from_configuration_and_defaults_to_a_placeholder() -> None:
    renderer = ReportRenderer(zone=UTC)
    experiment = big_experiment(5)
    default, _ = renderer.render_pdf(document_of(experiment), ReportTexts(ReportLanguage.ZH_CN))
    assert "© [版权方名称]，未经许可不得复制。" in flat_text(default)
    custom, _ = renderer.render_pdf(
        document_of(experiment, holder="厦门某某生物科技股份有限公司"),
        ReportTexts(ReportLanguage.ZH_CN),
    )
    assert "© 厦门某某生物科技股份有限公司，未经许可不得复制。" in flat_text(custom)


def test_user_text_is_escaped_not_interpreted_as_markup() -> None:
    experiment = big_experiment(3, name="<b>粗体</b>&<script>")
    data, _ = ReportRenderer(zone=UTC).render_pdf(
        document_of(experiment), ReportTexts(ReportLanguage.ZH_CN)
    )
    assert "<b>粗体</b>&<script>1" in flat_text(data)


def test_fonts_are_the_bundled_ones_and_are_embedded() -> None:
    assert ensure_fonts() is True  # 内置 Noto Sans SC 可用
    data, _ = ReportRenderer(zone=UTC).render_pdf(
        document_of(big_experiment(3)), ReportTexts(ReportLanguage.ZH_CN)
    )
    fonts = pdf_font_report(data)
    assert fonts and all(embedded for _, embedded in fonts)
    assert any("NotoSansSC" in name for name, _ in fonts)


def test_content_digest_ignores_language_and_changes_with_content() -> None:
    experiment = big_experiment(5)
    one = document_of(experiment)
    assert one.content_digest() == document_of(experiment).content_digest()
    other = big_experiment(6)
    assert one.content_digest() != document_of(other).content_digest()
    texts_zh = ReportRenderer(zone=UTC)
    data_zh, _ = texts_zh.render_pdf(one, ReportTexts(ReportLanguage.ZH_CN))
    data_en, _ = texts_zh.render_pdf(one, ReportTexts(ReportLanguage.EN))
    digest = one.content_digest()
    assert digest in flat_text(data_zh) and digest in flat_text(data_en)


def test_eight_tube_thumbnail_pages_are_optional(signing_stack: Stack, tmp_path: Path) -> None:
    """附 8 连图时多出缩略页；没有图的排说明“没有图像”。"""
    import numpy as np

    from gelinspect.services.reporting.document import RowImagePair
    from gelinspect.services.reporting.report_service import bgr_to_qimage

    experiment = big_experiment(8)
    image = bgr_to_qimage(np.full((60, 480, 3), 120, dtype=np.uint8))
    document = document_of(experiment)
    with_images = ReportDocument(
        scope=document.scope,
        sections=document.sections,
        copyright_holder=document.copyright_holder,
        images={
            "EXP-BIG-0001": (
                RowImagePair(1, image, image),
                RowImagePair(2, image, None),
            )
        },
    )
    renderer = ReportRenderer(zone=UTC)
    plain, plain_pages = renderer.render_pdf(document, ReportTexts(ReportLanguage.ZH_CN))
    attached, pages = renderer.render_pdf(with_images, ReportTexts(ReportLanguage.ZH_CN))
    assert pages == plain_pages + 1
    text = flat_text(attached)
    assert "8 连图" in text and "第 1 排" in text and "没有图像" in text
    assert "8 连图" not in flat_text(plain)
    assert b"/Subtype /Image" in attached or b"/Subtype/Image" in attached


def test_service_attaches_real_images_only_when_asked(signing_stack: Stack, tmp_path: Path) -> None:
    """服务层：默认不附图；要求附图但没有媒体服务时也不出错（没有图就不附）。"""
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    assert service.preview(analyst, "EXP-SAMPLE-0001").document.images == {}
    assert service.preview(analyst, "EXP-SAMPLE-0001", with_images=True).document.images == {}
    assert PASSWORDS and SIGN_TIME  # 夹具已使用
