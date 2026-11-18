"""新试验向导：全流程、试剂锁定、管位数量、扫码枪、权限、返回不丢数据、离开确认、启动失败。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QMenu

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, ExperimentType, ReagentKind, TubeRole
from gelinspect.domain.plate import (
    HOLES_PER_ROW,
    ROW_COUNT,
    TUBE_COUNT,
    position_to_tube,
    tube_to_position,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate
from gelinspect.interfaces.storage import AuditQuery, ExperimentQuery, PageRequest, StorageBusyError
from gelinspect.services.auth import Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import NewExperimentRequest
from gelinspect.services.template_service import TemplateDraft, build_template_config
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.new_experiment import (
    COL_BATCH,
    COL_INDEX,
    COL_LAMBDA,
    COL_NAME,
    COL_TUBE,
    NewExperimentPage,
)
from gelinspect.ui.pages.template_editor import TemplateEditorDialog
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.plate_grid import PlateMode
from simulation.reagents import SAMPLE_CODES
from simulation.sim_device import SimDevice
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in, wait_for
from tests.ui.wizard_helpers import (
    GOOD,
    STEP_CONFIRM,
    STEP_REAGENTS,
    STEP_SAMPLES,
    STEP_TEMPLATE,
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    scan,
    select_tubes,
)

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
LIMIT = BUILTIN_TEMPLATES[ExperimentType.LIMIT]


def audit_actions(context: AppContext, action: AuditAction) -> list[str]:
    page = context.database.audit.query(AuditQuery(action=action), PageRequest(limit=500))
    return [r.event.detail for r in sorted(page.items, key=lambda r: r.seq)]


def experiments(context: AppContext) -> list[str]:
    return [
        s.experiment_id
        for s in context.database.experiments.query(ExperimentQuery(), PageRequest()).items
    ]


def reach_samples(
    app: QApplication, page: NewExperimentPage, name: str = "灵敏度复核 (2λ:1)"
) -> None:
    choose_template(app, page, name)
    click(app, page.template_next)
    pass_reagents(app, page)
    click(app, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES


def reach_ten_tube_confirm(app: QApplication, page: NewExperimentPage) -> None:
    reach_samples(app, page)
    select_tubes(app, page, range(2, 10))
    row = next(row for row in page._draft.rows if row.tube_no == 1)
    row.batch_no = "BATCH-KEEP"
    page._sync_samples()
    click(app, page.sample_next)
    assert page.current_step() == STEP_CONFIRM
    assert page._draft.expected_count == 10


def click_sample_header(app: QApplication, page: NewExperimentPage, column: int) -> None:
    page.sample_table.scrollToItem(page.sample_table.cell_item(0, column))
    settle(app)
    header = page.sample_table.horizontalHeader()
    point = QPoint(
        header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
        header.height() // 2,
    )
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
    settle(app)


def sample_header_text(page: NewExperimentPage, column: int) -> str:
    item = page.sample_table.horizontalHeaderItem(column)
    assert item is not None
    return item.text()


def right_click_sample_row(app: QApplication, page: NewExperimentPage, point: QPoint) -> None:
    viewport = page.sample_table.viewport()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, viewport.mapToGlobal(point))
    app.sendEvent(viewport, event)
    settle(app)


def test_sample_context_menu_delete_keeps_multiselection_and_ignores_blank(
    qapp: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    click_sample_header(qapp, page, COL_TUBE)
    click_sample_header(qapp, page, COL_TUBE)
    original_ids = {row.row_id for row in page._draft.rows}
    deleted_ids = {page._draft.rows[0].row_id, page._draft.rows[1].row_id}
    selection = page.sample_table.selectionModel()
    assert selection is not None
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(page.sample_table.model().index(0, 0), flags)
    selection.select(page.sample_table.model().index(1, 0), flags)
    menu_actions: list[str] = []
    selected_when_opened: list[set[int]] = []

    def delete_from_menu(menu: QMenu, *_args: object) -> None:
        menu_actions.append(menu.actions()[0].text())
        selected_when_opened.append({index.row() for index in page.sample_table.selectedIndexes()})
        menu.actions()[0].trigger()

    monkeypatch.setattr(QMenu, "exec", delete_from_menu)
    hit = page.sample_table.visualItemRect(page.sample_table.cell_item(0, COL_NAME)).center()
    right_click_sample_row(qapp, page, hit)

    assert menu_actions == ["删除选中试管"]
    assert selected_when_opened == [{0, 1}]
    assert {row.row_id for row in page._draft.rows} == original_ids - deleted_ids
    assert set(page._draft.selected) == {row.tube_no for row in page._draft.rows if row.tube_no}

    menu_actions.clear()
    selection.clearSelection()

    def dismiss_menu(menu: QMenu, *_args: object) -> None:
        menu_actions.append(menu.actions()[0].text())

    monkeypatch.setattr(QMenu, "exec", dismiss_menu)
    hit = page.sample_table.visualItemRect(page.sample_table.cell_item(1, COL_NAME)).center()
    right_click_sample_row(qapp, page, hit)
    assert {index.row() for index in page.sample_table.selectedIndexes()} == {1}
    before_blank = len(menu_actions)
    page._show_sample_context_menu(QPoint(-1, -1))
    assert len(menu_actions) == before_blank


def test_sample_context_delete_confirms_edits_and_removes_unbound_rows(
    qapp: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    row = page._draft.rows[0]
    row.batch_no = "LOT-KEEP"
    page._sync_samples()
    decisions = iter([False])
    monkeypatch.setattr(
        ConfirmDialog,
        "ask",
        staticmethod(lambda *_args, **_kwargs: next(decisions)),
    )

    def trigger_delete(menu: QMenu, *_args: object) -> None:
        menu.actions()[0].trigger()

    monkeypatch.setattr(QMenu, "exec", trigger_delete)
    point = page.sample_table.visualItemRect(page.sample_table.cell_item(0, COL_NAME)).center()
    right_click_sample_row(qapp, page, point)
    assert page._scan_row(row.row_id) is row and row.batch_no == "LOT-KEEP"

    unbound = page._draft.add_row(name="未绑定样品")
    page._sync_samples()
    row_index = page._draft.rows.index(unbound)
    page.sample_table.scrollToItem(page.sample_table.cell_item(row_index, COL_NAME))
    point = page.sample_table.visualItemRect(
        page.sample_table.cell_item(row_index, COL_NAME)
    ).center()
    right_click_sample_row(qapp, page, point)
    assert page._scan_row(unbound.row_id) is None


# ---------------------------------------------------------------------------- 全流程


def test_full_flow_starts_the_engine_with_the_selected_tubes(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_wizard(qapp, window)
    assert page.current_step() == STEP_TEMPLATE and not page.template_next.isEnabled()
    reach_samples(qapp, page)

    # 第 3 步：孔板默认选中模板管位；改为 10–27 号后编辑首个样品批号
    table = page.sample_table
    tubes = list(range(10, 28))
    select_tubes(qapp, page, [*range(1, 10), *range(19, 28)])
    assert page.draft().selected == tubes
    assert [table.cell_text(i, COL_TUBE) for i in range(18)] == [str(t) for t in tubes]
    table.editItem(table.cell_item(0, COL_BATCH))
    editor = table.findChild(QLineEdit)
    assert editor is not None
    QTest.keyClicks(editor, "S2611-01")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    settle(qapp)
    assert page.draft().rows[0].batch_no == "S2611-01"
    assert page.plate.mode() is PlateMode.SELECT
    assert page.match_label.text() == "管位已对应" and page.sample_next.isEnabled()
    click(qapp, page.sample_next)

    # 第 4 步：汇总，确认后启动
    assert page.current_step() == STEP_CONFIRM
    assert page.summary_table.row_count() == 18
    assert page.confirm_start.isEnabled() and page.confirm_start.text() == "直接启动"
    click(qapp, page.confirm_start)
    assert wait_for(qapp, lambda: window.current_route == "monitor", 30.0)

    # 引擎收到的管位 = 选的管位，且按 domain 的映射分排
    snapshot = app_context.engine.snapshot()
    assert snapshot is not None and snapshot.state is ExperimentState.INCUBATING
    rows = {row.row_index: list(row.tubes) for row in snapshot.rows if row.tubes}
    expected: dict[int, list[int]] = {}
    for tube in tubes:
        expected.setdefault(tube_to_position(tube).row_index, []).append(tube)
    assert rows == expected
    stored = app_context.database.experiments.get(snapshot.experiment_id)
    assert stored is not None
    assert [a.tube_no for a in stored.assignments] == tubes
    assert stored.assignments[0].sample.batch_no == "S2611-01"
    assert stored.operator == "experimenter"
    # 向导回到干净的第一步；扫码、创建都留了审计
    assert page.current_step() == STEP_TEMPLATE and not page.is_dirty()
    assert len(audit_actions(app_context, AuditAction.REAGENT_CHECKED)) >= 4
    assert any("创建试验" in d for d in audit_actions(app_context, AuditAction.EXPERIMENT_CREATED))
    app_context.engine.stop(window.session.user, "测试结束")  # type: ignore[union-attr]
    assert app_context.engine.wait(30.0)


# ---------------------------------------------------------------------------- 步骤 1


def test_templates_are_listed_with_type_and_count(qapp: QApplication, window: MainWindow) -> None:
    page = open_wizard(qapp, window)
    options = {o.template.name: o for o in page._options}
    assert len(options) == 6
    for name, count in [
        ("灵敏度复核 (2λ:1)", 18),
        ("干扰试验", 28),
        ("半定量试验", 20),
        ("限度试验", 8),
        ("限度试验（复试）", 10),
        ("通用检测（96 管）", 96),
    ]:
        option = options[name]
        assert f"{count} 管" in option.meta.text()
        assert not hasattr(option, "badge")  # 没有“布局待确认”标记
    choose_template(qapp, page, "干扰试验")
    assert page.template_next.isEnabled()
    click(qapp, page.template_next)
    assert page.current_step() == STEP_REAGENTS and page.draft().expected_count == 28


def test_a_custom_template_made_by_the_admin_shows_up_in_the_wizard(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    from gelinspect.services.template_service import TemplateDraft, TemplateItemInput

    sign_in(qapp, window, "admin")
    admin = window.session
    app_context.templates.create_template(
        admin, TemplateDraft("我的限度", ExperimentType.LIMIT, [TemplateItemInput("样品")] * 4)
    )
    window.logout()
    page = open_wizard(qapp, window)
    names = [o.template.name for o in page._options]
    assert names[-1] == "我的限度"
    custom = page._options[-1]
    assert "自定义" in custom.meta.text()


# ---------------------------------------------------------------------------- 步骤 2：试剂


@pytest.mark.parametrize(
    ("key", "reason"),
    [("lal_expired", "已于 2026-01-31 过期"), ("lal_unregistered", "未备案")],
)
def test_expired_or_unregistered_reagent_locks_the_flow_with_the_reason_and_audits_it(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    key: str,
    reason: str,
) -> None:
    page = open_wizard(qapp, window)
    choose_template(qapp, page, "灵敏度复核 (2λ:1)")
    click(qapp, page.template_next)
    scan(qapp, page, ReagentKind.ENDOTOXIN, GOOD[ReagentKind.ENDOTOXIN])
    scan(qapp, page, ReagentKind.WATER, GOOD[ReagentKind.WATER])
    scan(qapp, page, ReagentKind.LAL_REAGENT, SAMPLE_CODES[key])

    assert page.is_locked() and not page.reagents_ok()
    assert page.lock_banner.isVisibleTo(page)
    assert "流程已锁定" in page.lock_banner.title.text()
    assert reason in page.lock_banner.detail.text()  # 红色说明原因
    assert page.lock_banner.property("tone") == "danger"
    assert page.reagent_cards[ReagentKind.LAL_REAGENT].property("state") == "bad"
    assert page.reagent_cards[ReagentKind.LAL_REAGENT].badge.text() == "已锁定"
    assert not page.reagent_next.isEnabled()
    click(qapp, page.reagent_next)  # 点禁用的按钮没有效果
    page._go(STEP_SAMPLES)  # 绕过按钮直接前进也不行
    assert page.current_step() == STEP_REAGENTS
    assert any("流程锁定" in d for d in audit_actions(app_context, AuditAction.REAGENT_CHECKED))
    dialog = page.lock_dialog  # 同时弹窗提示（不阻塞），关闭后才能继续操作
    assert dialog is not None and dialog.isVisible()
    assert dialog.title_label.text() == "试剂未通过校验"
    assert reason in dialog.message_label.text() and "流程已锁定" in dialog.message_label.text()
    dialog.close_button.click()
    settle(qapp)
    assert page.lock_dialog is None

    # 换上合格的批次后解除锁定，不再弹窗
    scan(qapp, page, ReagentKind.LAL_REAGENT, GOOD[ReagentKind.LAL_REAGENT])
    assert not page.is_locked() and page.reagents_ok()
    assert not page.lock_banner.isVisibleTo(page)
    assert page.reagent_next.isEnabled()
    assert page.lock_dialog is None


def test_the_service_refuses_even_when_the_wizard_is_bypassed(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    """界面被绕过：假装三种试剂都通过了，但草稿里放着过期的码，确认时服务层仍拒绝并回到锁定。"""
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page.plate.set_selected(page.draft().auto_select())
    click(qapp, page.sample_next)
    assert page.current_step() == STEP_CONFIRM
    page.draft().reagent_codes[ReagentKind.LAL_REAGENT] = SAMPLE_CODES["lal_expired"]
    click(qapp, page.confirm_start)
    assert experiments(app_context) == []  # 没有创建任何试验
    assert app_context.engine.snapshot() is None  # 引擎没有被启动
    assert page.current_step() == STEP_REAGENTS and page.is_locked()
    assert "过期" in page.lock_banner.detail.text()
    assert page.lock_dialog is not None and "过期" in page.lock_dialog.message_label.text()
    page.lock_dialog.close()
    assert any(
        "创建试验被拒绝" in d for d in audit_actions(app_context, AuditAction.REAGENT_CHECKED)
    )


def test_wrong_kind_and_unreadable_codes_are_rejected_without_locking(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    choose_template(qapp, page, "限度试验")
    click(qapp, page.template_next)
    scan(qapp, page, ReagentKind.WATER, SAMPLE_CODES["lal_valid"])  # 鲎试剂的码扫进了检查用水
    card = page.reagent_cards[ReagentKind.WATER]
    assert "不是检查用水" in card.message.text() and card.message.isVisibleTo(page)
    assert not page.is_locked() and card.scan() is not None and not card.scan().valid  # type: ignore[union-attr]
    scan(qapp, page, ReagentKind.WATER, "not-a-reagent-code")
    assert "无法识别" in card.message.text() and not page.is_locked()
    assert not page.reagent_next.isEnabled()


def test_keyboard_wedge_scanner_enter_triggers_the_check(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_wizard(qapp, window)
    choose_template(qapp, page, "限度试验")
    click(qapp, page.template_next)
    card = page.reagent_cards[ReagentKind.LAL_REAGENT]
    card.input.setFocus()
    QTest.keyClicks(card.input, SAMPLE_CODES["lal_valid"])  # 码打完但还没有回车：不触发
    assert card.scan() is None
    assert app_context.database.reagent_checks.query(PageRequest()).total == 0
    QTest.keyClick(card.input, Qt.Key.Key_Return)  # 扫码枪以回车结束
    settle(qapp)
    scanned = card.scan()
    assert scanned is not None and scanned.valid and scanned.batch_no == "LAL2606A"
    assert card.property("state") == "ok" and card.badge.text() == "已通过"
    # 回车后光标自动跳到下一张还没通过的卡，可以连续扫
    assert page.window().focusWidget() is page.reagent_cards[ReagentKind.ENDOTOXIN].input
    # 手输也一样；空输入的回车什么都不做
    QTest.keyClick(page.reagent_cards[ReagentKind.ENDOTOXIN].input, Qt.Key.Key_Return)
    assert page.reagent_cards[ReagentKind.ENDOTOXIN].scan() is None
    scan(qapp, page, ReagentKind.ENDOTOXIN, "  " + SAMPLE_CODES["endotoxin_valid"] + "  ")
    assert page.reagent_cards[ReagentKind.ENDOTOXIN].scan().valid  # type: ignore[union-attr]
    # 示例码按钮（模拟配置）：点一下填入并校验
    water = page.reagent_cards[ReagentKind.WATER]
    sample_buttons = water.samples_box.count()
    assert sample_buttons == 1
    sample_item = water.samples_box.itemAt(0)
    sample_button = sample_item.widget() if sample_item is not None else None
    assert sample_button is not None
    click(qapp, sample_button)
    assert water.scan() is not None and water.scan().valid  # type: ignore[union-attr]
    assert page.reagents_ok()


def test_manual_reagent_mode_and_editing_invalidate_old_pass(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    choose_template(qapp, page, "限度试验")
    click(qapp, page.template_next)
    card = page.reagent_cards[ReagentKind.LAL_REAGENT]
    scan(qapp, page, ReagentKind.LAL_REAGENT, SAMPLE_CODES["lal_valid"])
    original = card.scan()
    assert original is not None and original.valid
    card.manual_mode_button.click()
    settle(qapp)
    assert card.scan() is None and not page.reagents_ok()
    card.product_input.setText("GL-LAL-0.25")
    card.batch_input.setText("LAL2606A")
    card.scan_button.click()
    settle(qapp)
    manual = card.scan()
    assert manual is not None and manual.valid
    assert manual.input_mode == "manual"
    assert card.sensitivity_input.text() == "0.25"
    card.batch_input.setText("LAL9999X")
    settle(qapp)
    assert card.scan() is None and not page.reagents_ok()


# ---------------------------------------------------------------------------- 步骤 3：样品和管位


def test_plate_add_and_remove_keep_sample_rows_bound_to_tubes(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    assert page.draft().selected == list(range(1, 19))
    assert page.draft().expected_count == 18 and page.sample_next.isEnabled()
    original = {row.sequence_no: row.row_id for row in page.draft().rows}

    select_tubes(qapp, page, [18])
    assert page.draft().expected_count == 17
    assert page.draft().selected == list(range(1, 18))
    assert page.sample_next.isEnabled()
    assert {row.sequence_no: row.row_id for row in page.draft().rows} == {
        sequence: row_id for sequence, row_id in original.items() if sequence < 18
    }

    select_tubes(qapp, page, [19])
    assert page.draft().expected_count == 18 and page.draft().selected_count == 18
    assert page.match_label.text() == "管位已对应" and page.sample_next.isEnabled()
    assert page.match_label.property("role") == "ok"


def test_auto_select_and_clear(qapp: QApplication, window: MainWindow) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page, "限度试验")
    click(qapp, page.auto_button)
    assert page.plate.selected_tubes() == list(range(1, 9)) and page.sample_next.isEnabled()
    assert [page.sample_table.cell_text(i, COL_TUBE) for i in range(8)] == [
        str(t) for t in range(1, 9)
    ]
    click(qapp, page.clear_button)
    assert page.plate.selected_tubes() == [] and not page.sample_next.isEnabled()
    assert page.draft().expected_count == 8 and len(page.draft().rows) == 8
    assert page.sample_table.cell_text(0, COL_TUBE) == "–"


def test_plate_selection_uses_the_domain_numbering(qapp: QApplication, window: MainWindow) -> None:
    """孔板上第 r 列第 h 个孔就是 domain.plate 的 (排 r, 孔 h)；点击得到的管号与之一致。"""
    page = open_wizard(qapp, window)
    reach_samples(qapp, page, "限度试验")
    plate = page.plate
    for row in (1, 2, 5, ROW_COUNT):
        for hole in (1, HOLES_PER_ROW):
            tube = position_to_tube(row, hole)
            rect = plate.well_rect(tube)
            assert plate.tube_at(rect.center()) == tube
            column = int(rect.center().x() // (plate.width() / ROW_COUNT)) + 1
            assert column == row == tube_to_position(tube).row_index
    plate.clear_selection()
    select_tubes(qapp, page, [position_to_tube(3, 2), position_to_tube(ROW_COUNT, 8)])
    assert page.draft().selected == [18, TUBE_COUNT]


def test_editing_samples_and_blank_names_block_the_next_step(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page, "限度试验")
    click(qapp, page.auto_button)
    assert page.sample_next.isEnabled()
    table = page.sample_table
    table.cell_item(1, COL_NAME).setText("  ")
    assert "第 2 行的样品名称为空" in page.sample_problem.text()
    assert not page.sample_next.isEnabled()
    table.cell_item(1, COL_NAME).setText("样品二")
    table.cell_item(1, COL_LAMBDA).setText("0.5")
    assert not page.sample_problem.isVisibleTo(page) and page.sample_next.isEnabled()
    assert page.draft().rows[1].name == "样品二" and page.draft().rows[1].lambda_multiple == 0.5
    assert page._draft.rows[1].batch_no == ""


def test_going_back_keeps_everything_that_was_entered(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page.sample_table.cell_item(1, COL_NAME).setText("样品乙")
    page.sample_table.cell_item(1, COL_BATCH).setText("B-7")
    select_tubes(qapp, page, range(20, 38))
    click(qapp, page.sample_back)
    assert page.current_step() == STEP_REAGENTS and page.reagents_ok()
    lal = page.reagent_cards[ReagentKind.LAL_REAGENT]
    assert lal.scan() is not None and lal.result.batch.text() == "LAL2606A"
    click(qapp, page.reagent_back)
    assert page.current_step() == STEP_TEMPLATE
    assert next(o for o in page._options if o.template.name == "灵敏度复核 (2λ:1)").property(
        "selected"
    )
    click(qapp, page.template_next)
    click(qapp, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES
    assert page.plate.selected_tubes() == list(range(20, 38))
    assert page.sample_table.cell_text(1, COL_NAME) == "样品乙"
    assert page.sample_table.cell_text(1, COL_BATCH) == "B-7"
    assert page.sample_table.cell_text(0, COL_TUBE) == "20"
    click(qapp, page.sample_next)
    click(qapp, page.confirm_back)
    assert page.current_step() == STEP_SAMPLES and page.draft().selected == list(range(20, 38))


def test_sample_sort_keeps_stable_sequence_and_tube_bindings(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page._draft.clear_selection()
    rows = sorted(page._draft.rows, key=lambda row: row.sequence_no)
    for row, tube in zip(rows, reversed(range(1, len(rows) + 1)), strict=True):
        page._draft.assign_tube(row.row_id, tube)
    page._sync_samples()
    bindings = {row.sequence_no: (row.row_id, row.tube_no) for row in page._draft.rows}

    assert page.sample_table.isSortingEnabled() is False
    assert page.sample_table.horizontalHeader().sectionsClickable()
    click_sample_header(qapp, page, COL_TUBE)
    assert page.sample_table.cell_text(0, COL_INDEX) == str(len(rows))
    assert page.sample_table.cell_text(len(rows) - 1, COL_INDEX) == "1"
    assert sample_header_text(page, COL_TUBE) == "管号 ↑"
    click_sample_header(qapp, page, COL_TUBE)
    assert sample_header_text(page, COL_TUBE) == "管号 ↓"
    click_sample_header(qapp, page, COL_NAME)
    assert sample_header_text(page, COL_TUBE) == "管号 ↓"
    assert sample_header_text(page, COL_INDEX) == "序号 ↕"
    assert page.sample_table.cell_text(0, COL_INDEX) == str(len(rows))
    click_sample_header(qapp, page, COL_INDEX)
    assert sample_header_text(page, COL_INDEX) == "序号 ↑"
    assert [page.sample_table.cell_text(i, COL_INDEX) for i in range(len(rows))] == [
        str(i) for i in range(1, len(rows) + 1)
    ]
    assert {row.sequence_no: (row.row_id, row.tube_no) for row in page._draft.rows} == bindings


def test_adding_tube_keeps_current_tube_sort_order(qapp: QApplication, window: MainWindow) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page._draft.clear_selection()
    rows = sorted(page._draft.rows, key=lambda row: row.sequence_no)
    for row, tube in zip(rows[:3], (1, 3, 4), strict=True):
        page._draft.assign_tube(row.row_id, tube)
    page._sync_samples()
    click_sample_header(qapp, page, COL_TUBE)

    select_tubes(qapp, page, [2])

    assert [page.sample_table.cell_text(i, COL_TUBE) for i in range(4)] == [
        "1",
        "2",
        "3",
        "4",
    ]


def test_scanner_overwrites_in_sequence_and_single_row_resumes_batch_pointer(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page._draft.rows[0].batch_no = "OLD-1"
    page._draft.rows[1].batch_no = "OLD-2"
    page._draft.rows[4].batch_no = "OLD-5"
    page._sync_samples()
    edit_triggers = page.sample_table.editTriggers()

    page.batch_scan_button.click()
    assert page._batch_scan_active
    assert not page.batch_fill.isHidden()
    assert QApplication.focusWidget() is page.batch_fill
    assert page.sample_table.active_row == 0
    assert page.batch_scan_status.text() == "批量 #1"
    assert page.sample_table.editTriggers() == page.sample_table.EditTrigger.NoEditTriggers

    QTest.keyClicks(page.batch_fill, "LOT-1")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._draft.rows[0].batch_no == "LOT-1"
    assert page.sample_table.active_row == 1 and page.batch_scan_status.text() == "批量 #2"

    clicked = page.sample_table.visualItemRect(page.sample_table.cell_item(4, COL_NAME))
    QTest.mouseClick(page.sample_table.viewport(), Qt.MouseButton.LeftButton, pos=clicked.center())
    assert page.batch_scan_status.text() == "单扫 #5"
    assert page.sample_table.active_row == 4
    QTest.keyClicks(page.batch_fill, "LOT-SINGLE")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._draft.rows[4].batch_no == "LOT-SINGLE"
    assert page.sample_table.active_row == 1
    assert page.batch_scan_status.text() == "批量 #2"

    QTest.keyClicks(page.batch_fill, "LOT-2")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._draft.rows[1].batch_no == "LOT-2"

    # 单扫目标等于批量游标时，完成后推进一次，避免下一个批号覆盖同一行。
    target = page.sample_table.visualItemRect(page.sample_table.cell_item(1, COL_NAME))
    QTest.mouseClick(page.sample_table.viewport(), Qt.MouseButton.LeftButton, pos=target.center())
    assert page.batch_scan_status.text() == "单扫 #2"
    QTest.keyClicks(page.batch_fill, "LOT-2-AGAIN")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._draft.rows[1].batch_no == "LOT-2-AGAIN"
    assert page._batch_scan_row_id == page._draft.rows[2].row_id
    assert page.batch_scan_status.text() == "批量 #3"

    for sequence in range(3, len(page._draft.rows) + 1):
        QTest.keyClicks(page.batch_fill, f"LOT-{sequence}")
        QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._batch_scan_row_id is None and not page.batch_fill.isEnabled()

    # 批量结束后单扫仍可用，但不重新启动批量序号。
    target = page.sample_table.visualItemRect(page.sample_table.cell_item(9, COL_NAME))
    QTest.mouseClick(page.sample_table.viewport(), Qt.MouseButton.LeftButton, pos=target.center())
    assert page.batch_scan_status.text() == "单扫 #10" and page.batch_fill.isEnabled()
    QTest.keyClicks(page.batch_fill, "LOT-10-OVERRIDE")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    assert page._batch_scan_row_id is None
    assert page.batch_scan_status.text() == "已扫完；点结束" and not page.batch_fill.isEnabled()

    page.batch_scan_button.click()
    assert page.batch_fill.isHidden()
    assert page.sample_table.editTriggers() == edit_triggers
    assert page.sample_table.active_row is None


def test_scanner_sequence_is_independent_of_tube_column_sort(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    click_sample_header(qapp, page, COL_TUBE)
    click_sample_header(qapp, page, COL_TUBE)
    first = next(row for row in page._draft.rows if row.sequence_no == 1)
    first_index = page._draft.rows.index(first)
    assert page.sample_table.cell_text(first_index, COL_INDEX) == "1"

    page.batch_scan_button.click()

    assert page.batch_scan_status.text() == "批量 #1"
    assert page.sample_table.active_row == first_index
    QTest.keyClicks(page.batch_fill, "LOT-FIRST")
    QTest.keyClick(page.batch_fill, Qt.Key.Key_Return)
    second = next(row for row in page._draft.rows if row.sequence_no == 2)
    assert page.batch_scan_status.text() == "批量 #2"
    assert page.sample_table.active_row == page._draft.rows.index(second)


def test_plate_add_and_remove_preserves_stable_sequence_ids(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    before = {row.sequence_no: row.row_id for row in page._draft.rows}
    select_tubes(qapp, page, [25])
    assert page.draft().expected_count == 19
    assert page.draft().selected_count == 19
    added = next(row for row in page._draft.rows if row.tube_no == 25)
    assert added.sequence_no == 19 and added.name == "样品"

    select_tubes(qapp, page, [25])
    assert page.draft().expected_count == 18
    assert 25 not in page.draft().selected
    assert {row.sequence_no: row.row_id for row in page.draft().rows} == before

    select_tubes(qapp, page, [19])
    added_again = max(page.draft().rows, key=lambda row: row.sequence_no)
    assert added_again.sequence_no == 20 and added_again.row_id != added.row_id
    assert added_again.tube_no == 19

    index = page.draft().rows.index(added_again)
    page.sample_table.cell_item(index, COL_BATCH).setText("LOT-NEEDS-CONFIRM")
    asked: list[str] = []

    def decline(_parent: object, title: str, *_args: object, **_kwargs: object) -> bool:
        asked.append(title)
        return False

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(decline))
    select_tubes(qapp, page, [19])
    assert any(row.row_id == added_again.row_id for row in page.draft().rows)
    assert asked == ["删除样品行？"]

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_args, **_kwargs: True))
    select_tubes(qapp, page, [19])
    assert all(row.row_id != added_again.row_id for row in page.draft().rows)
    template = page.draft().template
    assert template is not None
    configured = page._current_configuration_draft(template)
    assert len(configured.items) == page.draft().expected_count == 17
    assert configured.tube_numbers == tuple(range(1, 18))


def test_deleting_pending_scan_row_advances_by_stable_sequence(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page.batch_scan_button.click()
    assert page.sample_table.active_row == 0 and page.batch_scan_status.text() == "批量 #1"

    def trigger_delete(menu: QMenu, *_args: object) -> None:
        menu.actions()[0].trigger()

    monkeypatch.setattr(QMenu, "exec", trigger_delete)
    point = page.sample_table.visualItemRect(page.sample_table.cell_item(0, COL_NAME)).center()
    right_click_sample_row(qapp, page, point)

    assert page._batch_scan_row_id == page._draft.rows[0].row_id
    assert page._draft.rows[0].sequence_no == 2
    assert page.sample_table.active_row == 0 and page.batch_scan_status.text() == "批量 #2"


def test_changing_the_template_asks_before_discarding_entered_samples(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page, "限度试验")
    click(qapp, page.auto_button)
    asked: list[str] = []

    def decline(_parent: object, title: str, *_a: object, **_k: object) -> bool:
        asked.append(title)
        return False

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(decline))
    click(qapp, page.change_template)
    assert page.current_step() == STEP_TEMPLATE
    choose_template(qapp, page, "限度试验（复试）")
    assert asked == ["更换模板？"] and page.draft().template.name == "限度试验"  # type: ignore[union-attr]
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_a, **_k: True))
    choose_template(qapp, page, "限度试验（复试）")
    assert page.draft().template.name == "限度试验（复试）"  # type: ignore[union-attr]
    assert page.draft().selected == list(range(1, 11))
    assert page.reagents_ok()  # 试剂不受模板影响


# ---------------------------------------------------------------------------- 离开确认


def test_leaving_an_unfinished_wizard_asks_for_confirmation(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    assert window.navigate("dashboard")  # 什么都没做：直接离开，不打扰（对话框默认会让测试失败）
    assert window.navigate("new")
    reach_samples(qapp, page, "限度试验")
    click(qapp, page.auto_button)
    asked: list[str] = []
    answer = {"value": False}

    def ask(_parent: object, title: str, *_a: object, **_k: object) -> bool:
        asked.append(title)
        return answer["value"]

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(ask))
    assert not window.navigate("dashboard")  # 选择留下
    assert window.current_route == "new" and asked == ["离开新建实验向导？"]
    assert page.draft().selected == list(range(1, 9)) and page.current_step() == STEP_SAMPLES
    # 快捷栏和侧栏走同一个入口
    window.shell.navigationRequested.emit("templates")
    assert window.current_route == "new" and len(asked) == 2
    answer["value"] = True
    assert window.navigate("templates")  # 确认离开
    assert window.current_route == "templates"
    assert window.navigate("new")  # 回来是干净的第一步
    assert page.current_step() == STEP_TEMPLATE and not page.is_dirty()


def test_logging_out_with_an_unfinished_wizard_asks_too(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    choose_template(qapp, page, "限度试验")
    click(qapp, page.template_next)
    answers = iter([False, True])
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_a, **_k: next(answers)))
    assert not window.logout() and window.session is not None
    assert window.logout() and window.session is None


# ---------------------------------------------------------------------------- 启动失败


def test_a_failed_start_shows_the_reason_and_can_be_retried(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    click(qapp, page.auto_button)
    click(qapp, page.sample_next)
    device = app_context.device
    assert isinstance(device, SimDevice)
    device.inject_temperature_excursion(+3.0)  # 温度越限，启动前检查失败
    click(qapp, page.confirm_start)
    assert wait_for(
        qapp,
        lambda: (
            page.start_banner.isVisibleTo(page) and "启动失败" in page.start_banner.title.text()
        ),
        30.0,
    )
    assert "温度" in page.start_banner.detail.text()  # 明确原因
    assert page.start_banner.property("tone") == "danger"
    assert window.current_route == "new" and page.current_step() == STEP_CONFIRM
    assert page.confirm_start.isEnabled() and page.confirm_start.text() == "重试启动"
    [only] = experiments(app_context)
    stored = app_context.database.experiments.get(only)
    assert stored is not None and stored.state is ExperimentState.READY

    assert app_context.engine.wait(30.0)
    device.clear_faults()
    click(qapp, page.confirm_start)  # 重试：不会再创建第二个试验
    assert wait_for(qapp, lambda: window.current_route == "monitor", 30.0)
    assert experiments(app_context) == [only]
    app_context.engine.stop(window.session.user, "测试结束")  # type: ignore[union-attr]
    assert app_context.engine.wait(30.0)


def test_save_template_and_start_uses_current_sparse_rows_and_reuses_saved_template(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window, "admin")
    reach_ten_tube_confirm(qapp, page)
    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    page.set_session(admin)
    saved_drafts: list[TemplateDraft] = []
    requests: list[NewExperimentRequest] = []
    started_ids: list[str] = []

    def create_template(_session: Session | None, draft: TemplateDraft) -> ExperimentTemplate:
        saved_drafts.append(draft)
        return build_template_config("custom.saved-wizard-template", draft)

    def create_experiment(_session: Session | None, request: NewExperimentRequest) -> str:
        requests.append(request)
        return "experiment-saved-template"

    def start_experiment(_session: Session | None, experiment_id: str) -> None:
        started_ids.append(experiment_id)

    monkeypatch.setattr(page._services.templates, "create_template", create_template)
    monkeypatch.setattr(page._services.experiments, "create_experiment", create_experiment)
    monkeypatch.setattr(page._services.experiments, "start_experiment", start_experiment)

    def accept_name(dialog: QInputDialog) -> int:
        dialog.setTextValue("稀疏10管模板")
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(QInputDialog, "exec", accept_name)
    page.confirm_save_start.click()
    settle(qapp)

    assert page._starting
    assert page.confirm_start.text() == "直接启动"
    assert len(saved_drafts) == len(requests) == len(started_ids) == 1
    [saved] = saved_drafts
    [request] = requests
    assert len(saved.items) == len(request.assignments) == 10
    assert tuple(saved.tube_numbers or ()) == (1, *range(10, 19))
    assert request.template_id == "custom.saved-wizard-template"
    assert request.template_snapshot is not None
    assert request.template_snapshot.template_id == request.template_id
    assert next(a for a in request.assignments if a.tube_no == 1).batch_no == "BATCH-KEEP"
    assert page._draft.rows[0].batch_no == "BATCH-KEEP"

    # 启动失败后点同一入口只重试启动，不重复保存或创建。
    page._starting = False
    page._start_error = "模拟设备启动失败"
    page._render()
    page.confirm_save_start.click()
    settle(qapp)
    assert len(saved_drafts) == len(requests) == 1
    assert started_ids == ["experiment-saved-template", "experiment-saved-template"]


def test_save_template_cancel_or_failure_never_starts_experiment(
    qapp: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window, "admin")
    reach_ten_tube_confirm(qapp, page)
    saves: list[TemplateDraft] = []
    creates: list[NewExperimentRequest] = []
    starts: list[str] = []

    def save_template(_session: Session | None, draft: TemplateDraft) -> ExperimentTemplate:
        saves.append(draft)
        raise ServiceError("duplicate", ServiceErrorCode.TEMPLATE_NAME_DUPLICATE)

    def create_experiment(_session: Session | None, request: NewExperimentRequest) -> str:
        creates.append(request)
        return "unexpected"

    def start_experiment(_session: Session | None, experiment_id: str) -> None:
        starts.append(experiment_id)

    monkeypatch.setattr(page._services.templates, "create_template", save_template)
    monkeypatch.setattr(page._services.experiments, "create_experiment", create_experiment)
    monkeypatch.setattr(page._services.experiments, "start_experiment", start_experiment)
    monkeypatch.setattr(
        QInputDialog,
        "exec",
        lambda _dialog: int(QDialog.DialogCode.Rejected),
    )
    page._save_template_and_start()
    assert not saves and not creates and not starts

    monkeypatch.setattr(
        QInputDialog,
        "exec",
        lambda _dialog: int(QDialog.DialogCode.Accepted),
    )
    page._save_template_and_start()
    assert len(saves) == 1
    assert not creates and not starts and not page._starting


def test_saved_launch_permission_requires_template_management(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
) -> None:
    from gelinspect.domain.enums import PermissionGroup
    from gelinspect.ui.pages.new_experiment import NewExperimentPage as Page

    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    user = app_context.database.users.get_by_username("analyst")
    assert user is not None
    app_context.users.set_permission_groups(
        admin,
        user.user_id,
        frozenset({PermissionGroup.VIEW, PermissionGroup.EXPERIMENT}),
    )
    sign_in(qapp, window, "analyst")
    page = Page(window.services, window.bridge)
    page.set_session(window.session)
    page._draft.choose_template(BUILTIN_TEMPLATES[ExperimentType.LIMIT])
    assert page.can_start() and page.confirm_start.isEnabled()
    assert not page.confirm_save_start.isEnabled()
    assert page.confirm_save_start.toolTip() == "需要模板管理、创建和启动权限"
    page.deleteLater()


def test_save_template_button_requires_effective_template_changes(
    qapp: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window, "admin")
    page._draft.choose_template(LIMIT)
    page._show_step(STEP_CONFIRM)
    assert not page.confirm_save_start.isEnabled()

    page._draft.rows[0].batch_no = "BATCH-ONLY"
    page._draft.sort_rows("tube_no", reverse=True)
    page._render()
    assert not page.confirm_save_start.isEnabled()

    calls: list[TemplateDraft] = []
    monkeypatch.setattr(
        page._services.templates,
        "create_template",
        lambda _session, draft: calls.append(draft),
    )
    page._save_template_and_start()
    assert not calls and page._created_id is None

    page._draft.rows[0].name = "更改样品名"
    page._render()
    assert page.confirm_save_start.isEnabled()

    blank_draft = replace(LIMIT, template_id="draft.blank-configuration")
    page._draft.choose_template(blank_draft)
    page._render()
    assert page.confirm_save_start.isEnabled()
    page._draft.clear_selection()
    page._render()
    assert not page.confirm_save_start.isEnabled()


def test_save_template_change_detection_uses_effective_values_not_row_order(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window, "admin")
    page._draft.choose_template(LIMIT)
    row = next(item for item in page._draft.rows if item.sequence_no == 1)
    assert not page._template_config_changed()

    page._draft.sort_rows("tube_no", reverse=True)
    assert not page._template_config_changed()
    page._draft.sort_rows("sequence_no")

    original_name = row.name
    row.name = "不同样品"
    assert page._template_config_changed()
    row.name = original_name
    assert not page._template_config_changed()

    row.role = TubeRole.NEGATIVE_CONTROL
    assert page._template_config_changed()
    row.role = LIMIT.items[0].role
    row.dilution_factor = 9.0
    assert page._template_config_changed()
    row.dilution_factor = LIMIT.items[0].dilution_factor
    row.spiked_endotoxin = 0.9
    assert page._template_config_changed()
    row.spiked_endotoxin = LIMIT.items[0].spiked_endotoxin
    row.lambda_multiple = 0.5
    assert page._template_config_changed()
    row.lambda_multiple = LIMIT.items[0].lambda_multiple
    row.spiked_endotoxin = LIMIT.items[0].spiked_endotoxin
    row.tube_no = 9
    assert page._template_config_changed()
    row.tube_no = LIMIT.items[0].tube_no
    assert not page._template_config_changed()

    page._draft.add_row(name="未绑定行")
    assert not page._template_config_changed()

    page._draft.remove_row(row.row_id)
    page._draft.add_row(
        name=LIMIT.items[0].sample_name,
        role=LIMIT.items[0].role,
        tube_no=LIMIT.items[0].tube_no,
    )
    assert not page._template_config_changed()

    page._draft.choose_template(replace(LIMIT, experiment_type=ExperimentType.GENERAL))
    assert page._template_config_changed()


def test_abandon_failure_keeps_confirm_draft_and_blocks_back_navigation(
    qapp: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window)
    reach_ten_tube_confirm(qapp, page)
    page._created_id = "ready-experiment"
    page._start_error = "模拟设备启动失败"
    before = [(row.row_id, row.name, row.batch_no, row.tube_no) for row in page._draft.rows]

    def fail_abandon(*_args: object, **_kwargs: object) -> None:
        raise StorageBusyError("数据库忙")

    monkeypatch.setattr(page._services.experiments, "abandon_experiment", fail_abandon)
    page._back_from_confirm()

    assert page.current_step() == STEP_CONFIRM
    assert page._created_id == "ready-experiment"
    assert [(row.row_id, row.name, row.batch_no, row.tube_no) for row in page._draft.rows] == before
    assert page.confirm_start.text() == "重试启动"
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_args, **_kwargs: True))
    assert not page.confirm_leave()
    assert not page.reset()
    assert page._created_id == "ready-experiment"
    assert [(row.row_id, row.name, row.batch_no, row.tube_no) for row in page._draft.rows] == before


@pytest.mark.parametrize("lang", ["zh_CN", "zh_TW", "en"])
def test_confirm_actions_are_grouped_on_the_right_at_common_window_widths(
    qapp: QApplication,
    window: MainWindow,
    language: object,
    lang: str,
) -> None:
    from gelinspect.services.appearance_service import AppearancePreferences
    from gelinspect.ui.i18n import Language, LanguageManager
    from gelinspect.ui.theme.appearance import apply_appearance

    assert isinstance(language, LanguageManager)
    page = open_wizard(qapp, window)
    language.set_language(Language(lang), persist=False)
    apply_appearance(qapp, AppearancePreferences(font_scale="large"))
    try:
        page._show_step(STEP_CONFIRM)
        page.show()
        for width in (1024, 1280):
            window.resize(width, 828)
            settle(qapp)
            back_x = page.confirm_back.mapTo(page, QPoint(0, 0)).x()
            save_pos = page.confirm_save_start.mapTo(page, QPoint(0, 0))
            start_pos = page.confirm_start.mapTo(page, QPoint(0, 0))
            assert back_x < save_pos.x()
            assert save_pos.y() == start_pos.y()
            assert save_pos.x() + page.confirm_save_start.width() + 24 <= start_pos.x()
            assert page.confirm_save_start.width() >= page.confirm_save_start.sizeHint().width()
            assert page.confirm_start.width() >= page.confirm_start.sizeHint().width()
            assert start_pos.x() + page.confirm_start.width() <= page.width()
    finally:
        apply_appearance(qapp, AppearancePreferences())
        language.set_language(Language.ZH_CN, persist=False)


def test_going_back_after_a_failed_start_abandons_the_unstarted_experiment(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    click(qapp, page.auto_button)
    click(qapp, page.sample_next)
    device = app_context.device
    assert isinstance(device, SimDevice)
    device.inject_temperature_excursion(+3.0)
    click(qapp, page.confirm_start)
    assert wait_for(qapp, lambda: "启动失败" in page.start_banner.title.text(), 30.0)
    assert app_context.engine.wait(30.0)
    click(qapp, page.confirm_back)  # 回去修改：旧试验作废
    [only] = experiments(app_context)
    stored = app_context.database.experiments.get(only)
    assert stored is not None and stored.state is ExperimentState.STOPPED
    assert not page.start_banner.isVisibleTo(page)
    device.clear_faults()
    click(qapp, page.sample_next)
    click(qapp, page.confirm_start)
    assert wait_for(qapp, lambda: window.current_route == "monitor", 30.0)
    assert len(experiments(app_context)) == 2  # 旧的已放弃，新的启动
    app_context.engine.stop(window.session.user, "测试结束")  # type: ignore[union-attr]
    assert app_context.engine.wait(30.0)


# ---------------------------------------------------------------------------- 权限


def test_experiment_group_users_can_open_the_wizard(qapp: QApplication, window: MainWindow) -> None:
    for username, allowed in (("experimenter", True), ("analyst", True), ("admin", True)):
        sign_in(qapp, window, username)
        assert window.navigate("new") is allowed, username
        assert window.shell.toolbar.button("new_experiment").isEnabled() is allowed
        if allowed:
            assert window.current_route == "new"
        else:
            assert window.current_route == "dashboard"
        window.logout()


def test_without_the_run_permission_the_confirm_button_is_disabled(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    """页面被直接构造给没有权限的会话：扫码和启动按钮禁用；服务层另行拒绝（见服务测试）。"""
    from gelinspect.domain.enums import PermissionGroup
    from gelinspect.ui.pages.new_experiment import NewExperimentPage as Page

    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    user = app_context.database.users.get_by_username("analyst")
    assert user is not None
    app_context.users.set_permission_groups(
        admin,
        user.user_id,
        frozenset({PermissionGroup.VIEW}),
    )
    sign_in(qapp, window, "analyst")
    page = Page(window.services, window.bridge)
    page.set_session(window.session)
    assert not page.can_start()
    assert not page.reagent_cards[ReagentKind.WATER].input.isEnabled()
    assert not page.confirm_start.isEnabled()
    assert page.confirm_start.toolTip() == "当前角色无此权限"
    assert not page.confirm_save_start.isEnabled()
    assert page.confirm_save_start.toolTip() == "需要模板管理、创建和启动权限"
    page.deleteLater()


def test_blank_configuration_is_a_per_run_draft(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window)

    def save_dialog(dialog: TemplateEditorDialog) -> int:
        dialog.name.setText("本次空白配置")
        dialog.table.cell_item(0, 1).setText("样品甲")
        dialog.save_button.click()
        return int(dialog.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", save_dialog)
    page._configure_blank()
    template = page.draft().template
    assert template is not None and template.template_id.startswith("draft.")
    assert template.tube_count == 1 and template.items[0].sample_name == "样品甲"
    assert app_context.database.templates.get(template.template_id) is None


def test_reediting_configuration_preserves_source_and_current_sample_text(
    qapp: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_wizard(qapp, window)
    page.draft().choose_template(LIMIT)
    page._sync_samples()
    dialogs = 0

    def save_dialog(dialog: TemplateEditorDialog) -> int:
        nonlocal dialogs
        dialogs += 1
        if dialogs == 1:
            dialog.name.setText("本次修改")
            dialog.table.cell_item(0, 1).setText("样品首版")
        else:
            assert dialog.table.cell_text(0, 1) == "样品最新值"
        dialog.save_button.click()
        return int(dialog.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", save_dialog)
    monkeypatch.setattr(ConfirmDialog, "ask", lambda *_args, **_kwargs: True)
    page._configure_selected()
    first = page.draft().template
    assert first is not None and page.draft().source_template_id == LIMIT.template_id
    page.draft().rows[0].name = "样品最新值"
    page._sync_samples()
    page._configure_selected()
    request = page.draft().to_request()
    assert dialogs == 2
    assert request.source_template_id == LIMIT.template_id
    assert request.template_snapshot is not None
    assert request.template_snapshot.items[0].sample_name == "样品最新值"


def test_cancel_or_save_as_template_keeps_current_draft(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_wizard(qapp, window, "admin")
    page.draft().choose_template(LIMIT)
    page.draft().rows[0].name = "当前样品值"
    page._sync_samples()
    original_template = page.draft().template
    original_rows = list(page.draft().rows)

    def cancel_dialog(_dialog: TemplateEditorDialog) -> int:
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(TemplateEditorDialog, "exec", cancel_dialog)
    page._save_as_template()
    assert page.draft().template is original_template
    assert page.draft().rows == original_rows

    def save_dialog(dialog: TemplateEditorDialog) -> int:
        assert dialog.table.cell_text(0, 1) == "当前样品值"
        dialog.name.setText("已另存模板")
        dialog.save_button.click()
        return int(dialog.result())

    monkeypatch.setattr(TemplateEditorDialog, "exec", save_dialog)
    page._save_as_template()
    saved = next(
        (item for item in app_context.database.templates.list_all() if item.name == "已另存模板"),
        None,
    )
    assert saved is not None and saved.items[0].sample_name == "当前样品值"
    assert page.draft().template is original_template
    assert page.draft().rows[0].name == "当前样品值"
