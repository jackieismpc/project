"""繁体中文报告：生成、签名、验签，文字是繁体，所有字符都来自内置字体。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QRawFont

from gelinspect.services.reporting.renderer import FONT_DIR
from gelinspect.services.reporting.report_service import ReportService
from gelinspect.services.reporting.texts import ReportLanguage, ReportTexts, report_language_of
from gelinspect.services.reporting.verify import verify_pdf_bytes
from tests.services.conftest import PASSWORDS, Stack

from .conftest import make_completed, make_service
from .pdftext import flat_text, pdf_font_report

TRADITIONAL_WORDS = (
    "自動凝膠檢測報告",
    "檔名（試驗編號）",
    "EXP-SAMPLE-0001",
    "試管編號",
    "樣品名稱",
    "序列號",
    "樣品批號",
    "稀釋倍數",
    "外加內毒素",
    "陽性（已複核）",
    "結論：合格",
    "λc = 0.5946",
    "本報告已使用數字簽名，任何修改都會導致驗簽失敗。",
    "SHA-256",
    "第 1 頁 / 共 1 頁",
    "靈敏度複核",  # 内置的默认样品名也有繁体
    "陰性對照",
)
USER_DATA = ("张工", "李工")
"""测试数据里的人名（操作人、签署人的显示名，用户输入），不翻译。"""


def simplified_only(text: str) -> list[str]:
    """简体独有的汉字：GB2312 能编码而 Big5 不能。"""
    found = []
    for char in text:
        if "一" <= char <= "鿿":
            try:
                char.encode("gb2312")
                char.encode("big5")
            except UnicodeEncodeError as exc:
                if exc.encoding.lower().startswith("big5"):
                    found.append(char)
    return found


def sign_in_traditional(stack: Stack, tmp_path: Path) -> tuple[ReportService, bytes, str]:
    make_completed(stack, operator="zhang")
    service, keystore = make_service(stack, tmp_path)
    result = service.sign(
        stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"], ReportLanguage.ZH_TW
    )
    data = result.path.read_bytes()
    verification = verify_pdf_bytes(
        data, keystore.trusted_certificate(), expected_sha256=result.record.sha256
    )
    assert verification.ok and verification.signature_intact
    return service, data, result.record.language


def test_traditional_pdf_is_generated_signed_and_verified(
    signing_stack: Stack, tmp_path: Path
) -> None:
    _, data, language = sign_in_traditional(signing_stack, tmp_path)
    assert language == "zh_TW"
    text = flat_text(data)
    for expected in TRADITIONAL_WORDS:
        assert expected in text, expected
    assert "�" not in text
    for name in USER_DATA:
        text = text.replace(name, "")
    assert simplified_only(text) == [], "繁体报告里不应出现简体字"
    for bracket in "\u300c\u300d":
        assert bracket not in text


def test_traditional_pdf_uses_only_bundled_fonts_and_every_glyph_is_covered(
    qapp: object, signing_stack: Stack, tmp_path: Path
) -> None:
    """没有任何字符回退到系统字体：PDF 里的字体都是内置的，所有字符都在内置字体里。"""
    _, data, _ = sign_in_traditional(signing_stack, tmp_path)
    fonts = pdf_font_report(data)
    assert fonts and all(embedded for _, embedded in fonts)
    for name, _ in fonts:
        assert "NotoSansSC" in name or "IBMPlexMono" in name, f"回退到了系统字体：{name}"
    covers = [
        QRawFont(str(FONT_DIR / name), 12)
        for name in ("NotoSansSC-Regular.ttf", "IBMPlexMono-Regular.ttf")
    ]
    uncovered = {
        c
        for c in flat_text(data)
        if not c.isspace() and not any(font.supportsCharacter(ord(c)) for font in covers)
    }
    assert uncovered == set()


def test_signed_report_keeps_its_traditional_language(signing_stack: Stack, tmp_path: Path) -> None:
    service, _, _ = sign_in_traditional(signing_stack, tmp_path)
    analyst = signing_stack.login("analyst")
    preview = service.preview(analyst, "EXP-SAMPLE-0001", ReportLanguage.EN)
    assert preview.language is ReportLanguage.ZH_TW  # 已签署的以签署时的语言为准


def test_every_report_text_has_a_traditional_translation() -> None:
    from gelinspect.services.reporting.strings import SOURCES

    texts = ReportTexts(ReportLanguage.ZH_TW)
    for source in SOURCES.values():
        translated = texts.tr(source)
        assert translated and simplified_only(translated) == [], (source, translated)
    assert texts.missing == frozenset()
    assert report_language_of("zh_TW") is ReportLanguage.ZH_TW


def test_traditional_preview_uses_traditional_report_texts(
    signing_stack: Stack, tmp_path: Path
) -> None:
    from gelinspect.services.reporting.strings import S

    make_completed(signing_stack)
    service, _ = make_service(signing_stack, tmp_path)
    analyst = signing_stack.login("analyst")
    preview = service.preview(analyst, "EXP-SAMPLE-0001", ReportLanguage.ZH_TW)
    assert preview.language is ReportLanguage.ZH_TW
    assert preview.texts.s(S.TITLE) == "自動凝膠檢測報告"
    zh_cn = service.preview(analyst, "EXP-SAMPLE-0001", ReportLanguage.ZH_CN)
    assert zh_cn.texts.s(S.TITLE) == "自动凝胶检测报告"
