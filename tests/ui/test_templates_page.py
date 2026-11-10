"""模板管理页：内置只读可复制，管理员新建、复制、编辑，其他角色只读，写审计。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, ExperimentType, TubeRole
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.template_editor import (
    COL_DILUTION,
    COL_ROLE,
    COL_SAMPLE,
    COL_SPIKE,
    TemplateEditorDialog,
)
from gelinspect.ui.pages.templates_page import TemplatesPage
from tests.ui.conftest import settle, sign_in
from tests.ui.wizard_helpers import click

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]


def open_templates(app: QApplication, win: MainWindow, username: str) -> TemplatesPage:
    sign_in(app, win, username)
    assert win.navigate("templates")
    page = win.page("templates")
    assert isinstance(page, TemplatesPage)
    settle(app)
    return page


def names(page: TemplatesPage) -> list[str]:
    return [i.template.name for i in page._infos]


def audit(context: AppContext) -> list[str]:
    result = context.database.audit.query(
        AuditQuery(action=AuditAction.TEMPLATE_CHANGED), PageRequest(limit=200)
    )
    return [r.event.detail for r in sorted(result.items, key=lambda r: r.seq)]


def select_row(app: QApplication, page: TemplatesPage, name: str) -> None:
    row = names(page).index(name)
    page.list_table.view.selectRow(row)
    settle(app)
    assert page.selected() is not None and page.selected().template.name == name  # type: ignore[union-attr]


def fake_dialog(
    monkeypatch: pytest.MonkeyPatch,
    edit: object,
    *,
    record: list[TemplateEditorDialog] | None = None,
) -> None:
    """替换对话框的 exec：先让 ``edit`` 改控件，再点“保存”。"""

    def run(self: TemplateEditorDialog) -> int:
        if record is not None:
            record.append(self)
        edit(self)  # type: ignore[operator]
        self.save_button.click()
        return int(self.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", run)


def test_lists_builtin_templates_with_type_count_and_state(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_templates(qapp, window, "admin")
    rows = page.list_table.model.rows
    assert [r["name"] for r in rows] == [
        "灵敏度复核 (2λ:1)", "干扰试验", "半定量试验", "限度试验", "限度试验（复试）",
        "通用检测（96 管）",
    ]  # fmt: skip
    assert [r["count"] for r in rows] == [18, 28, 20, 8, 10, 96]
    assert [r["source"] for r in rows] == ["内置"] * 6
    assert all("state" not in r for r in rows)  # 没有“布局待确认”列
    assert page.detail_table.row_count() == 18  # 默认选中第一个，显示它的管位
    select_row(qapp, page, "限度试验")
    assert page.detail_table.row_count() == 8
    assert [r["role"] for r in page.detail_table.model.rows] == [
        "供试品", "供试品", "供试品阳性对照", "供试品阳性对照",
        "阳性对照", "阳性对照", "阴性对照", "阴性对照",
    ]  # fmt: skip


def test_builtin_templates_are_read_only_but_can_be_copied(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_templates(qapp, window, "admin")
    select_row(qapp, page, "干扰试验")
    assert page.new_button.isEnabled() and page.copy_button.isEnabled()
    assert not page.edit_button.isEnabled()
    assert "只读" in page.edit_button.toolTip()
    page.edit_selected()  # 即使被直接调用也不会打开编辑
    click(qapp, page.copy_button)
    assert names(page)[-1] == "干扰试验 副本"
    copy = page.selected()
    assert copy is not None and not copy.builtin and copy.template.tube_count == 28
    assert not copy.template.needs_confirmation
    assert page.edit_button.isEnabled()  # 副本可以编辑
    assert any("复制模板 干扰试验 为 干扰试验 副本" in d for d in audit(app_context))
    original = app_context.database.templates.get(
        BUILTIN_TEMPLATES[ExperimentType.INTERFERENCE].template_id
    )
    assert original == BUILTIN_TEMPLATES[ExperimentType.INTERFERENCE]


def test_create_a_template_with_the_editor(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_templates(qapp, window, "admin")

    def edit(dialog: TemplateEditorDialog) -> None:
        dialog.name.setText("我的灵敏度")
        dialog.type_box.setCurrentIndex(dialog.type_box.findData(ExperimentType.LIMIT.value))
        table = dialog.table
        table.cell_item(0, COL_SAMPLE).setText("样品甲")
        table.cell_item(0, COL_DILUTION).setText("10")
        table.cell_item(0, COL_SPIKE).setText("0.5")
        dialog.add_button.click()
        table.cell_item(1, COL_SAMPLE).setText("阴性对照")
        table.cell_item(1, COL_ROLE).setData(0x100, TubeRole.NEGATIVE_CONTROL.value)  # UserRole

    fake_dialog(monkeypatch, edit)
    click(qapp, page.new_button)
    assert names(page)[-1] == "我的灵敏度"
    info = page.selected()
    assert info is not None and info.template.name == "我的灵敏度" and not info.builtin
    template = info.template
    assert template.experiment_type is ExperimentType.LIMIT and template.tube_count == 2
    first, second = sorted(template.items, key=lambda i: i.tube_no)
    assert (first.sample_name, first.dilution_factor, first.spiked_endotoxin) == (
        "样品甲",
        10.0,
        0.5,
    )
    assert second.role is TubeRole.NEGATIVE_CONTROL
    assert any("新建模板 我的灵敏度" in d for d in audit(app_context))
    assert page.detail_table.row_count() == 2


def test_the_editor_reports_errors_without_closing(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_templates(qapp, window, "admin")
    shown: list[str] = []

    def run(self: TemplateEditorDialog) -> int:
        self.name.setText("  ")
        self.table.cell_item(0, COL_SAMPLE).setText("样品")
        self.save_button.click()
        shown.append(self.error_label.text())
        assert self.error_label.isVisibleTo(self) and self.result() != QDialog.DialogCode.Accepted
        self.name.setText("干扰试验")  # 与内置同名
        self.save_button.click()
        shown.append(self.error_label.text())
        self.table.cell_item(0, COL_SAMPLE).setText("  ")
        self.name.setText("不同名")
        self.save_button.click()
        shown.append(self.error_label.text())
        self.cancel_button.click()
        return int(self.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", run)
    click(qapp, page.new_button)
    assert shown == ["模板名称不能为空", "已有同名模板：干扰试验", "第 1 支试管的样品名称不能为空"]
    assert len(names(page)) == 6  # 一个都没有保存


def test_edit_a_custom_template(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_templates(qapp, window, "admin")
    select_row(qapp, page, "限度试验")
    click(qapp, page.copy_button)
    seen: list[TemplateEditorDialog] = []

    def edit(dialog: TemplateEditorDialog) -> None:
        assert dialog.windowTitle() == "编辑模板"
        assert dialog.name.text() == "限度试验 副本" and dialog.table.rowCount() == 8
        dialog.name.setText("限度试验（改）")
        for _ in range(3):
            dialog.remove_button.click()
        dialog.table.cell_item(0, COL_SAMPLE).setText("供试品")

    fake_dialog(monkeypatch, edit, record=seen)
    click(qapp, page.edit_button)
    assert names(page)[-1] == "限度试验（改）"
    info = page.selected()
    assert info is not None and info.template.tube_count == 5
    assert info.template.items[0].sample_name == "供试品"
    assert any("编辑模板 限度试验（改）" in d for d in audit(app_context))
    # 删除到只剩一支后不能再删
    dialog = seen[0]
    assert dialog.table.rowCount() == 5


@pytest.mark.parametrize("username", ["experimenter", "analyst"])
def test_other_roles_can_only_view(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    username: str,
) -> None:
    page = open_templates(qapp, window, username)
    assert len(names(page)) == 6 and page.detail_table.row_count() == 18
    assert page.readonly_badge.isVisibleTo(page) and page.readonly_badge.text() == "只读"
    for button in (page.new_button, page.copy_button, page.edit_button):
        assert not button.isEnabled()
        assert button.toolTip() == "当前角色无此权限"
    assert "管理员" in page.note.text()
    # 页面方法被直接调用（界面被绕过）：服务层拒绝，没有保存
    errors: list[str] = []

    def run(self: TemplateEditorDialog) -> int:
        self.name.setText("越权模板")
        self.table.cell_item(0, COL_SAMPLE).setText("样品")
        self.save_button.click()
        errors.append(self.error_label.text())
        self.cancel_button.click()
        return int(self.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", run)
    page.create_template()
    assert errors == ["当前角色无此权限"]
    page.copy_selected()
    assert len(names(page)) == 6 and audit(app_context) == [
        d for d in audit(app_context) if "初始化内置模板" in d
    ]
    window.logout()
    assert window.session is None
