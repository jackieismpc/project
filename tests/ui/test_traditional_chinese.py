"""繁体中文（台湾用语）：翻译完整且用词统一、字体不回退、三种语言切换与持久化、系统语言默认值。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QLocale, QSettings
from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication

from gelinspect.ui.i18n import (
    Language,
    LanguageManager,
    MemoryLanguageSettings,
    QSettingsLanguageSettings,
    detect_system_language,
    ts_path,
)
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.punctuation import annotate, colon, join_items
from gelinspect.ui.theme.fonts import BUNDLED_FONT_FILES, FONT_DIR
from tests.ui.conftest import settle, sign_in

HAN = re.compile(r"[一-鿿]")
PLACEHOLDER = re.compile(r"\{\w+(?::[^}]*)?\}")
ROOT = Path(__file__).parents[2]


def tw_entries() -> list[tuple[str, str, str]]:
    """``(上下文, 原文, 译文)``。"""
    result = []
    for context in ET.parse(ts_path(Language.ZH_TW)).getroot().iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            translation = message.find("translation")
            text = "".join(translation.itertext()) if translation is not None else ""
            result.append((name, message.findtext("source") or "", text))
    return result


def simplified_only(text: str) -> list[str]:
    """文字里简体独有的汉字：GB2312 能编码而 Big5 不能。"""
    found = []
    for char in text:
        if HAN.match(char):
            try:
                char.encode("gb2312")
            except UnicodeEncodeError:
                continue
            try:
                char.encode("big5")
            except UnicodeEncodeError:
                found.append(char)
    return found


# ---------------------------------------------------------------- 翻译文件


def test_traditional_translations_are_complete_and_keep_placeholders() -> None:
    entries = tw_entries()
    simplified = ts_path(Language.ZH_CN)
    assert len(entries) == len(ET.parse(simplified).getroot().findall(".//message")) > 1000
    root = ET.parse(ts_path(Language.ZH_TW)).getroot()
    assert root.get("language") == "zh_TW" and root.get("sourcelanguage") == "zh_CN"
    for context, source, text in entries:
        assert text.strip(), f"未翻译：{context} / {source}"
        assert set(PLACEHOLDER.findall(source)) == set(PLACEHOLDER.findall(text)), (
            f"占位符不一致：{source!r} → {text!r}"
        )
        # 首尾的空格是拼接用的，必须保留
        assert source[:1].isspace() == text[:1].isspace(), f"开头空格：{source!r} → {text!r}"
        assert source[-1:].isspace() == text[-1:].isspace(), f"结尾空格：{source!r} → {text!r}"
    assert not any(
        not item.findtext("translation") or item.find("translation").get("type") == "unfinished"  # type: ignore[union-attr]
        for item in root.iter("message")
    )


def test_no_simplified_only_characters_remain_in_traditional_translations() -> None:
    for context, source, text in tw_entries():
        assert simplified_only(text) == [], f"译文里还有简体字：{context} / {source} → {text!r}"


def test_quotes_are_curly_double_quotes_never_corner_brackets() -> None:
    text = "".join(t for _, _, t in tw_entries())
    for bracket in "\u300c\u300d\u300e\u300f\ufe41\ufe42":
        assert bracket not in text
    assert "“" in text and "”" in text  # 例如“重置”“待定”
    for _, source, translation in tw_entries():
        assert source.count("“") == translation.count("“"), f"{source!r} → {translation!r}"
        assert source.count("”") == translation.count("”"), f"{source!r} → {translation!r}"


def test_traditional_punctuation_is_full_width() -> None:
    """汉字旁边不出现半角的逗号、分号、冒号、问号、感叹号和括号。"""
    pattern = re.compile(r"[一-鿿][,;:!?()]|[,;:!?()][一-鿿]")
    for context, source, text in tw_entries():
        if "antilg(" in source or ";;" in source:
            continue  # 数学公式和文件对话框的过滤器（``;;`` 分隔），符号必须是半角
        assert not pattern.search(text), f"半角标点：{context} → {text!r}"


GLOSSARY = {
    ("SideNav", "审计追踪"): "稽核軌跡",
    ("SideNav", "工作台"): "工作台",
    ("SideNav", "报告中心"): "報告中心",
    ("SideNav", "模板管理"): "範本管理",
    ("Interpretation", "阳性"): "陽性",
    ("Interpretation", "阴性"): "陰性",
    ("Interpretation", "待复核"): "待複核",
    ("ReagentKind", "鲎试剂"): "鱟試劑",
    ("ReagentKind", "内毒素"): "內毒素",
    ("GelState", "完全凝固"): "完全凝固",
    ("Experiment", "单管结果"): "單管結果",
    ("Role", "管理员"): "管理員",
    ("Role", "分析员"): "分析員",
    ("Role", "实验员"): "實驗員",
    ("LoginPage", "登录"): "登入",
    ("LoginPage", "退出登录"): "登出",
    ("LoginPage", "用户名"): "使用者名稱",
    ("AuthError", "当前角色无此权限"): "目前角色無此權限",
    (
        "Signing",
        "试验已签署，结果和复核已锁定，不能再修改。",
    ): "試驗已簽署，結果和複核已鎖定，不能再修改。",
    ("Report", "自动凝胶检测报告"): "自動凝膠檢測報告",
}
"""术语表里的代表条目（完整术语表见 docs/features/auth-and-i18n.md）。"""

BANNED = (
    "審計",  # 统一用“稽核”
    "軟件",
    "數據",
    "視頻",
    "打印",
    "信息",
    "默認",
    "質量",
    "鏈接",
    "用戶",
    "服務器",
    "屏幕",
    "文件夾",
    "賬",
    "許可權",
    "透過",  # 通过的误转换
    "嚮導",
    "模板",  # 统一用“範本”
    "告警",  # 统一用“警示”
    "校驗",  # 统一用“驗證”
)
"""大陆用语或不统一的写法，繁体译文里不应出现。"""


def test_glossary_terms_are_used_consistently() -> None:
    table = {(c, s): t for c, s, t in tw_entries()}
    wanted = {key: value for key, value in GLOSSARY.items() if key in table}
    assert len(wanted) >= len(GLOSSARY) - 4, "术语表条目找不到对应的原文，请更新测试"
    for key, value in wanted.items():
        assert table[key] == value, key
    everything = "".join(t for _, _, t in tw_entries())
    for term in BANNED:
        assert term not in everything, f"不应出现的写法：{term}"
    for term in ("鱟試劑", "內毒素", "凝膠", "靈敏度", "陰性", "陽性", "複核", "試管", "孔板"):
        assert term in everything, f"术语表里的写法没有出现：{term}"


# ---------------------------------------------------------------- 字体


def raw_font(name: str) -> QRawFont:
    font = QRawFont(str(FONT_DIR / name), 16)
    assert font.isValid(), name
    return font


def missing_from(font: QRawFont, chars: set[str]) -> list[str]:
    return sorted(c for c in chars if not font.supportsCharacter(ord(c)))


def test_every_traditional_character_is_in_the_bundled_font(qapp: QApplication) -> None:
    """繁体界面里没有任何字符回退到系统字体：每个字符都在内置字体的 cmap 里。"""
    chars = {c for _, _, text in tw_entries() for c in text if not c.isspace()}
    assert len(chars) > 500
    for name in BUNDLED_FONT_FILES[:3]:
        missing = missing_from(raw_font(name), chars)
        assert missing == [], f"{name} 缺字：{''.join(missing)}"


def gb2312_hanzi() -> set[str]:
    chars = set()
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def big5_level1_hanzi() -> set[str]:
    chars = set()
    for high in range(0xA4, 0xC7):
        for low in [*range(0x40, 0x7F), *range(0xA1, 0xFF)]:
            code = bytes([high, low])
            if code > bytes([0xC6, 0x7E]):
                continue
            try:
                chars.add(code.decode("big5"))
            except UnicodeDecodeError:
                continue
    return chars


def test_the_font_covers_gb2312_and_the_common_traditional_set(qapp: QApplication) -> None:
    gb, big5 = gb2312_hanzi(), big5_level1_hanzi()
    assert len(gb) == 6763 and len(big5) == 5401
    for name in BUNDLED_FONT_FILES[:3]:
        font = raw_font(name)
        assert missing_from(font, gb) == []
        assert missing_from(font, big5) == []  # 台湾常用字，覆盖常用国字标准字体表的 4808 字
        assert missing_from(font, {"\u2212", "ㄅ", "\u3000", "“", "”", "（", "，"}) == []


def test_font_assets_stay_small_and_keep_their_licenses() -> None:
    total = sum(p.stat().st_size for p in FONT_DIR.glob("*.ttf"))
    assert total < 10_500_000  # 补字前约 7.5 MB（Noto 7.35 MB），补繁体后约 9.9 MB，增量要小于 8 MB
    assert "SIL OPEN FONT LICENSE" in (FONT_DIR / "OFL-NotoSansSC.txt").read_text()
    assert (FONT_DIR / "OFL-IBMPlexMono.txt").is_file()


# ---------------------------------------------------------------- 切换与持久化


def test_cycle_through_three_languages(qapp: QApplication, language: LanguageManager) -> None:
    seen: list[str] = []
    language.languageChanged.connect(seen.append)
    assert language.available() == (Language.ZH_CN, Language.ZH_TW, Language.EN)
    assert language.options() == [("zh_CN", "简体中文"), ("zh_TW", "繁體中文"), ("en", "English")]
    expected = {
        Language.ZH_CN: "审计追踪",
        Language.ZH_TW: "稽核軌跡",
        Language.EN: "Audit trail",
    }
    for lang in (Language.ZH_TW, Language.EN, Language.ZH_CN, Language.EN, Language.ZH_TW):
        assert language.set_language(lang)
        assert QCoreApplication.translate("SideNav", "审计追踪") == expected[lang]
    assert seen == ["zh_TW", "en", "zh_CN", "en", "zh_TW"]


def test_choice_of_traditional_chinese_is_persisted(qapp: QApplication, tmp_path: Path) -> None:
    ini = tmp_path / "gelinspect.ini"

    def make() -> LanguageManager:
        return LanguageManager(
            qapp,
            settings=QSettingsLanguageSettings(QSettings(str(ini), QSettings.Format.IniFormat)),
        )

    first = make()
    first.set_language(Language.ZH_TW)
    assert "language=zh_TW" in ini.read_text()
    second = make()
    try:
        assert second.current is Language.ZH_TW
        assert QCoreApplication.translate("SideNav", "工作台") == "工作台"
        assert QCoreApplication.translate("SideNav", "报告中心") == "報告中心"
    finally:
        second.set_language(Language.ZH_CN, persist=False)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("zh_TW", Language.ZH_TW),
        ("zh_HK", Language.ZH_TW),
        ("zh_MO", Language.ZH_TW),
        ("zh_Hant_TW", Language.ZH_TW),
        ("zh_Hant", Language.ZH_TW),
        ("zh_CN", None),
        ("zh_Hans_SG", None),
        ("zh", None),
        ("en_US", None),
        ("ja_JP", None),
    ],
)
def test_system_language_detection(name: str, expected: Language | None) -> None:
    assert detect_system_language(QLocale(name)) is expected


def test_default_language_is_used_only_without_a_saved_choice(qapp: QApplication) -> None:
    fresh = LanguageManager(
        qapp, settings=MemoryLanguageSettings(), default_language=Language.ZH_TW
    )
    try:
        assert fresh.current is Language.ZH_TW
        saved = LanguageManager(
            qapp,
            settings=MemoryLanguageSettings(Language.EN),
            default_language=Language.ZH_TW,
        )
        assert saved.current is Language.EN
        assert QCoreApplication.translate("SideNav", "工作台") == "Dashboard"
    finally:
        fresh.set_language(Language.ZH_CN, persist=False)
    assert not MemoryLanguageSettings().load_language()


def test_default_language_falls_back_when_the_translation_is_missing(
    qapp: QApplication, tmp_path: Path
) -> None:
    manager = LanguageManager(
        qapp,
        settings=MemoryLanguageSettings(),
        directory=tmp_path,
        default_language=Language.ZH_TW,
    )
    assert manager.current is Language.ZH_CN


# ---------------------------------------------------------------- 窗口文字与标点


def test_window_texts_switch_to_traditional_chinese_immediately(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    page = window.login_page
    assert page.submit_button.text() == "登录"
    language.set_language(Language.ZH_TW)
    settle(qapp)
    assert page.submit_button.text() == "登入"
    assert page._user_label.text() == "使用者名稱"
    assert page._password_label.text() == "密碼"
    assert page._demo_rows[0].role.text() == "管理員"
    assert window.windowTitle() == "凝膠智檢系統 GelInspect"
    sign_in(qapp, window, "experimenter")
    toolbar = window.shell.toolbar
    assert toolbar.action("new_experiment").text() == "新增試驗"
    assert toolbar._user.text() == "Jordan Li · 實驗員"
    assert toolbar._language.text() == "繁體中文"
    assert [i.text() for i in window.shell.nav.items()][:3] == ["工作台", "新試驗", "試驗監控"]
    language.set_language(Language.ZH_CN)
    settle(qapp)
    assert toolbar._language.text() == "简体中文"
    assert toolbar.action("new_experiment").text() == "新建试验"


def test_an_error_on_screen_switches_to_traditional_chinese(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    window.login_page.username.setText("admin")
    window.login_page.password.setText("nope-nope")
    window.login_page.submit_button.click()
    language.set_language(Language.ZH_TW)
    settle(qapp)
    assert window.login_page.error_label.text() == "使用者名稱或密碼錯誤，還可以嘗試 4 次"


def test_punctuation_helpers_stay_full_width_in_traditional_chinese(
    qapp: QApplication, language: LanguageManager
) -> None:
    language.set_language(Language.ZH_TW)
    assert annotate("新增試驗", "Ctrl+N") == "新增試驗（Ctrl+N）"
    assert colon("類型", "限度試驗") == "類型：限度試驗"
    assert join_items([1, 2, 3]) == "1、2、3"
    language.set_language(Language.EN)
    assert annotate("New", "Ctrl+N") == "New (Ctrl+N)"
    assert join_items([1, 2, 3]) == "1, 2, 3"


def test_alerts_templates_and_dialogs_have_traditional_text(
    qapp: QApplication, language: LanguageManager
) -> None:
    language.set_language(Language.ZH_TW)
    translate = QCoreApplication.translate
    assert translate("Alert", "视频断流") == "影片串流中斷"
    assert translate("Alert", "画面过暗（黑屏）") == "畫面過暗（黑屏）"
    assert translate("Startup", "配置有误，无法启动") == "設定有誤，無法啟動"
    assert translate("Startup", "数据库被占用") == "資料庫被佔用"
    assert translate("ErrorDialog", "技术细节") == "技術細節"
    assert (
        translate("LoginPage", "演示账号（模拟配置，点击填入）") != "演示账号（模拟配置，点击填入）"
    )
