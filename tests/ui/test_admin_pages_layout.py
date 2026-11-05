"""数据管理四页（历史、审计、用户、设置）在有数据和各种状态下，中英文 × 两种窗口尺寸都不超界。"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from gelinspect.app_context import AppContext
from gelinspect.services.user_settings import VideoInput
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.audit_page import AuditPage
from gelinspect.ui.pages.history_page import HistoryPage
from gelinspect.ui.pages.settings_page import SettingsPage
from gelinspect.ui.pages.users_page import UsersPage
from tests.ui.admin_helpers import seed_audit, seed_experiments, select_rows
from tests.ui.conftest import settle, sign_in, wait_for
from tests.ui.test_layout_languages import LANGUAGES, SIZES, overflow_report, use


def tight_placeholders(root: QWidget) -> list[str]:
    """输入框的占位文字要放得下：样式里左右各有约 10px 内边距，比通用检查更严。"""
    problems: list[str] = []
    for edit in root.findChildren(QLineEdit):
        if not edit.isVisibleTo(root) or not edit.placeholderText():
            continue
        needed = QFontMetrics(edit.font()).horizontalAdvance(edit.placeholderText()) + 26
        if needed > edit.width():
            problems.append(
                f"占位文字放不下：{edit.placeholderText()!r}（需要 {needed}，只有 {edit.width()}）"
            )
    return problems


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_history_and_audit_pages_with_data_fit(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
) -> None:
    seed_experiments(app_context, 24)
    seed_audit(app_context, 130)
    use(window, language, lang, size)
    sign_in(qapp, window, "admin")
    window.navigate("history")
    history = window.page("history")
    assert isinstance(history, HistoryPage)
    select_rows(history.table, [0, 1])
    assert overflow_report(window) == [], "历史（有数据，选中两行）"
    assert tight_placeholders(history) == []
    history.batch_edit.setText("没有这个批号")
    history.search()
    assert history.table.is_empty_state_visible()
    assert overflow_report(window) == [], "历史（空状态）"

    window.navigate("audit")
    audit = window.page("audit")
    assert isinstance(audit, AuditPage)
    assert wait_for(qapp, lambda: not audit.loading and audit.total > 0)
    assert overflow_report(window) == [], "审计（有数据）"
    assert tight_placeholders(audit) == []
    audit.verify_button.click()
    assert wait_for(qapp, lambda: not audit.verifying, 20)
    assert overflow_report(window) == [], "审计（校验通过横幅）"
    audit.keyword_edit.setText("没有这个关键字")
    audit.search()
    assert wait_for(qapp, lambda: not audit.loading)
    assert audit.table.is_empty_state_visible()
    assert overflow_report(window) == [], "审计（空状态）"
    audit.wait_idle()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_users_and_settings_pages_fit_in_every_state(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    use(window, language, lang, size)
    sign_in(qapp, window, "admin")
    window.navigate("users")
    users = window.page("users")
    assert isinstance(users, UsersPage)
    users.select_user("analyst")
    assert overflow_report(window) == [], "用户（选中他人）"
    users.select_user("admin")
    assert overflow_report(window) == [], "用户（选中自己，按钮置灰）"

    window.navigate("settings")
    settings = window.page("settings")
    assert isinstance(settings, SettingsPage)
    assert wait_for(qapp, lambda: not settings.previewing)
    assert overflow_report(window) == [], "设置（可编辑，预览当前视频源）"
    # 非法值：每项都出现错误提示
    for name in ("incubation_minutes", "frames_per_side", "temperature_target_c", "retention_days"):
        settings.fields[name].edit.setText("abc")
    settings.set_video_input(VideoInput.FILE, str(tmp_path_factory.mktemp("v") / "missing.mp4"))
    assert not settings.save()
    settle(qapp)
    assert overflow_report(window) == [], "设置（五项非法值的提示）"

    window.logout(confirm=False)
    sign_in(qapp, window, "analyst")
    window.navigate("settings")
    readonly = window.page("settings")
    assert isinstance(readonly, SettingsPage)
    assert wait_for(qapp, lambda: not readonly.previewing)
    assert not readonly.editable
    assert overflow_report(window) == [], "设置（只读角色）"
