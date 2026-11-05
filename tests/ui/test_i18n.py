"""多语言：切换立即生效、持久化、翻译文件完整、.qm 自动编译。"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

from gelinspect.ui.i18n import (
    LANGUAGE_NAMES,
    Language,
    LanguageManager,
    MemoryLanguageSettings,
    QSettingsLanguageSettings,
    compile_translations,
    find_lrelease,
    translations_dir,
    ts_path,
)
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.auth_pages import LoginMode
from gelinspect.ui.pages.placeholder import PlaceholderPage
from tests.ui.conftest import settle, sign_in

ROOT = Path(__file__).parents[2]


def test_source_language_needs_no_translation_file(qapp: QApplication) -> None:
    manager = LanguageManager(qapp, settings=MemoryLanguageSettings())
    assert manager.current is Language.ZH_CN
    assert QCoreApplication.translate("SideNav", "工作台") == "工作台"


def test_switching_translates_immediately_and_back(
    qapp: QApplication, language: LanguageManager
) -> None:
    seen: list[str] = []
    language.languageChanged.connect(seen.append)
    assert language.set_language(Language.EN)
    assert QCoreApplication.translate("SideNav", "工作台") == "Dashboard"
    assert language.set_language("zh_CN")
    assert QCoreApplication.translate("SideNav", "工作台") == "工作台"
    assert seen == ["en", "zh_CN"]
    assert not language.set_language("fr")  # 不支持的语言
    assert language.current is Language.ZH_CN


def test_only_simplified_chinese_and_english_exist() -> None:
    assert {lang.value for lang in Language} == {"zh_CN", "en"}
    assert LANGUAGE_NAMES == {Language.ZH_CN: "简体中文", Language.EN: "English"}
    assert not any("zh_TW" in p.name for p in translations_dir().iterdir())


def test_choice_is_persisted_across_restarts(qapp: QApplication, tmp_path: Path) -> None:
    ini = tmp_path / "gelinspect.ini"
    first = LanguageManager(
        qapp, settings=QSettingsLanguageSettings(QSettings(str(ini), QSettings.Format.IniFormat))
    )
    first.set_language(Language.EN)
    assert "language=en" in ini.read_text()
    # 模拟重启：新的管理器从同一个文件读取
    second = LanguageManager(
        qapp, settings=QSettingsLanguageSettings(QSettings(str(ini), QSettings.Format.IniFormat))
    )
    assert second.current is Language.EN
    assert QCoreApplication.translate("SideNav", "工作台") == "Dashboard"
    second.set_language(Language.ZH_CN)
    third = LanguageManager(
        qapp, settings=QSettingsLanguageSettings(QSettings(str(ini), QSettings.Format.IniFormat))
    )
    assert third.current is Language.ZH_CN


def test_invalid_saved_value_falls_back_to_the_source_language(
    qapp: QApplication, tmp_path: Path
) -> None:
    ini = tmp_path / "bad.ini"
    ini.write_text("[ui]\nlanguage=klingon\n")
    manager = LanguageManager(
        qapp, settings=QSettingsLanguageSettings(QSettings(str(ini), QSettings.Format.IniFormat))
    )
    assert manager.current is Language.ZH_CN


# ---------------------------------------------------------------- 窗口文字随语言变化


def test_login_page_texts_change(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    page = window.login_page
    assert page.submit_button.text() == "登录" and page._user_label.text() == "用户名"
    language.set_language(Language.EN)
    settle(qapp)
    assert page.submit_button.text() == "Sign in"
    assert page._user_label.text() == "Username"
    assert page._password_label.text() == "Password"
    assert window.windowTitle() == "GelInspect"
    assert page._demo_rows[0].role.text() == "Administrator"
    language.set_language(Language.ZH_CN)
    settle(qapp)
    assert page.submit_button.text() == "登录"
    assert page._demo_rows[0].role.text() == "管理员"


def test_an_error_on_screen_changes_language_too(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    window.login_page.username.setText("admin")
    window.login_page.password.setText("nope-nope")
    window.login_page.submit_button.click()
    assert "用户名或密码错误" in window.login_page.error_label.text()
    language.set_language(Language.EN)
    settle(qapp)
    assert window.login_page.error_label.text() == "Wrong username or password. 4 attempts left."


def test_shell_texts_change_everywhere(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    sign_in(qapp, window, "experimenter")
    toolbar = window.shell.toolbar
    nav = window.shell.nav
    assert toolbar.action("new_experiment").text() == "新建试验"
    assert toolbar._user.text() == "Jordan Li · 实验员"
    assert nav.items()[0].text() == "工作台"
    language.set_language(Language.EN)
    settle(qapp)
    assert toolbar.action("new_experiment").text() == "New experiment"
    assert toolbar.action("instrument_settings").text() == "Settings"
    assert toolbar._user.text() == "Jordan Li · Experimenter"
    assert toolbar._language.text() == "English"
    assert [i.text() for i in nav.items()][:3] == [
        "Dashboard",
        "New experiment",
        "Experiment monitor",
    ]
    assert nav._brand_label.text() == "GelInspect"
    assert "Not permitted" in toolbar.action("audit_trail").toolTip()
    dashboard = window.page("dashboard")
    assert dashboard is not None
    assert dashboard.title.text() == "Dashboard"  # type: ignore[attr-defined]
    assert window.windowTitle() == "Dashboard - GelInspect"
    # 已打开的占位页和之后新建的页面都是英文
    window.navigate("reports")
    page = window.page("reports")
    assert isinstance(page, PlaceholderPage)
    assert page.badge.text() == "Under construction"
    language.set_language(Language.ZH_CN)
    settle(qapp)
    assert page.badge.text() == "建设中"
    assert toolbar.action("new_experiment").text() == "新建试验"
    assert toolbar._user.text() == "Jordan Li · 实验员"


def test_language_menu_in_the_toolbar_switches_the_language(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    sign_in(qapp, window, "analyst")
    menu = window.shell.toolbar._language_menu
    labels = [a.text() for a in menu.actions()]
    assert labels == ["简体中文", "English"]
    assert [a.isChecked() for a in menu.actions()] == [True, False]
    menu.actions()[1].trigger()
    settle(qapp)
    assert language.current is Language.EN
    assert [a.isChecked() for a in menu.actions()] == [False, True]


def test_language_buttons_on_the_login_page(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    buttons = window.login_page.languages._buttons
    assert set(buttons) == {"zh_CN", "en"}
    buttons["en"].click()
    settle(qapp)
    assert language.current is Language.EN
    assert buttons["en"] is not None


def test_lock_screen_keeps_working_after_a_language_change(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    sign_in(qapp, window, "admin")
    window.lock_screen()
    language.set_language(Language.EN)
    settle(qapp)
    assert window.lock_page.mode() is LoginMode.LOCKED
    assert window.lock_page.heading.text() == "Screen locked"
    assert window.lock_page.brand_sub.text() == "Alex Chen · Administrator"
    assert window.lock_page.submit_button.text() == "Unlock"


# ---------------------------------------------------------------- 翻译文件


def messages(path: Path) -> list[tuple[str, str, str | None, bool]]:
    out = []
    for context in ET.parse(path).getroot().iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            translation = message.find("translation")
            text = "".join(translation.itertext()) if translation is not None else None
            unfinished = translation is not None and translation.get("type") == "unfinished"
            out.append((name, message.findtext("source") or "", text, unfinished))
    return out


def test_english_translations_are_complete_and_keep_placeholders() -> None:
    entries = messages(ts_path(Language.EN))
    assert len(entries) > 150
    for context, source, text, unfinished in entries:
        assert text and not unfinished, f"未翻译：{context} / {source}"
        assert set(re.findall(r"\{\w+\}", source)) == set(re.findall(r"\{\w+\}", text)), (
            f"占位符不一致：{source!r} → {text!r}"
        )
        assert not re.search(r"[一-鿿]", text), f"译文里还有中文：{text!r}"


def test_ts_files_are_up_to_date_with_the_sources() -> None:
    """源码里 tr() 文字变了而 .ts 没更新时失败；运行 scripts/update_translations.py 修复。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "update_translations.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_translation_script_is_importable_as_a_module() -> None:
    spec = importlib.util.spec_from_file_location(
        "update_translations", ROOT / "scripts" / "update_translations.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LANGUAGES == ("zh_CN", "en")


def test_qm_files_are_ignored_by_git() -> None:
    gitignore = (ROOT / ".gitignore").read_text()
    assert re.search(r"^\*\.qm$", gitignore, re.MULTILINE)


def test_missing_qm_is_compiled_on_demand(tmp_path: Path) -> None:
    assert find_lrelease() is not None
    for language in Language:
        shutil.copy(ts_path(language), tmp_path / ts_path(language).name)
    assert not list(tmp_path.glob("*.qm"))
    built = compile_translations(tmp_path)
    assert set(built) == {Language.EN}
    qm = built[Language.EN]
    assert qm.parent == tmp_path and qm.stat().st_size > 1000


def test_stale_qm_is_rebuilt_and_fresh_qm_is_kept(tmp_path: Path) -> None:
    shutil.copy(ts_path(Language.EN), tmp_path / ts_path(Language.EN).name)
    qm = compile_translations(tmp_path)[Language.EN]
    first = qm.stat().st_mtime_ns
    assert compile_translations(tmp_path)[Language.EN].stat().st_mtime_ns == first
    ts = tmp_path / ts_path(Language.EN).name
    os.utime(ts, ns=(first + 5_000_000_000, first + 5_000_000_000))
    assert compile_translations(tmp_path)[Language.EN].stat().st_mtime_ns > first


def test_a_clean_checkout_can_switch_language(qapp: QApplication, tmp_path: Path) -> None:
    """干净克隆里没有 .qm：管理器启动时自己编译，英文立即可用。"""
    for language in Language:
        shutil.copy(ts_path(language), tmp_path / ts_path(language).name)
    manager = LanguageManager(qapp, settings=MemoryLanguageSettings(), directory=tmp_path)
    try:
        assert manager.available() == (Language.ZH_CN, Language.EN)
        assert manager.set_language(Language.EN, persist=False)
        assert QCoreApplication.translate("SideNav", "审计追踪") == "Audit trail"
    finally:
        manager.set_language(Language.ZH_CN, persist=False)


def test_missing_translation_file_degrades_to_the_source_language(
    qapp: QApplication, tmp_path: Path
) -> None:
    manager = LanguageManager(qapp, settings=MemoryLanguageSettings(), directory=tmp_path)
    assert manager.available() == (Language.ZH_CN,)
    assert manager.set_language(Language.EN) is False
    assert manager.current is Language.ZH_CN
