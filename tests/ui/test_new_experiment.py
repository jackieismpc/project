"""新试验向导：全流程、试剂锁定、管位数量、扫码枪、权限、返回不丢数据、离开确认、启动失败。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, ExperimentType, ReagentKind
from gelinspect.domain.plate import (
    HOLES_PER_ROW,
    ROW_COUNT,
    TUBE_COUNT,
    position_to_tube,
    tube_to_position,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import AuditQuery, ExperimentQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.new_experiment import (
    COL_BATCH,
    COL_NAME,
    COL_SPIKE,
    COL_TUBE,
    NewExperimentPage,
)
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.plate_grid import PlateMode
from simulation.reagents import SAMPLE_CODES
from simulation.sim_device import SimDevice
from tests.ui.conftest import settle, sign_in, wait_for
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


# ---------------------------------------------------------------------------- 全流程


def test_full_flow_starts_the_engine_with_the_selected_tubes(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_wizard(qapp, window)
    assert page.current_step() == STEP_TEMPLATE and not page.template_next.isEnabled()
    reach_samples(qapp, page)

    # 第 3 步：编辑样品表（真实编辑器），在孔板上点 10–26 号管
    table = page.sample_table
    table.editItem(table.cell_item(0, COL_BATCH))
    editor = table.findChild(QLineEdit)
    assert editor is not None
    QTest.keyClicks(editor, "S2611-01")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    settle(qapp)
    assert page.draft().rows[0].batch_no == "S2611-01"
    assert page.plate.mode() is PlateMode.SELECT
    tubes = list(range(10, 27))
    select_tubes(qapp, page, tubes)
    assert page.draft().selected == tubes
    assert [table.cell_text(i, COL_TUBE) for i in range(17)] == [str(t) for t in tubes]
    assert page.match_label.text() == "管位已对应" and page.sample_next.isEnabled()
    click(qapp, page.sample_next)

    # 第 4 步：汇总，确认后启动
    assert page.current_step() == STEP_CONFIRM
    assert page.summary_table.row_count() == 17
    assert page.confirm_start.isEnabled() and page.confirm_start.text() == "确认并启动"
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


def test_templates_are_listed_with_type_count_and_the_unconfirmed_badge(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    options = {o.template.name: o for o in page._options}
    assert len(options) == 5
    for name, count, unconfirmed in [
        ("灵敏度复核 (2λ:1)", 17, False),
        ("干扰试验", 28, True),
        ("半定量试验", 16, True),
        ("限度试验", 8, True),
        ("限度试验（复试）", 10, True),
    ]:
        option = options[name]
        assert f"{count} 管" in option.meta.text()
        assert option.badge.isVisibleTo(page) is unconfirmed
        assert not unconfirmed or option.badge.text() == "布局待确认"
    # 布局待确认的模板仍可使用
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
    assert "自定义" in custom.meta.text() and not custom.badge.isVisibleTo(page)


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


# ---------------------------------------------------------------------------- 步骤 3：样品和管位


def test_count_mismatch_blocks_the_next_step_and_shows_the_difference(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    assert page.count_label.text() == "已选 0 / 17 ·"
    assert page.match_label.text() == "请在孔板上选择管位" and not page.sample_next.isEnabled()
    select_tubes(qapp, page, range(1, 17))  # 16 个
    assert page.count_label.text() == "已选 16 / 17 ·" and page.match_label.text() == "还差 1 个"
    assert not page.sample_next.isEnabled()
    click(qapp, page.sample_next)
    page._go(STEP_CONFIRM)  # 绕过按钮也不行
    assert page.current_step() == STEP_SAMPLES
    select_tubes(qapp, page, [17, 18])  # 18 个
    assert page.match_label.text() == "多选了 1 个" and not page.sample_next.isEnabled()
    select_tubes(qapp, page, [18])  # 取消 18
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
    table.cell_item(2, COL_NAME).setText("  ")
    assert "第 3 行的样品名称为空" in page.sample_problem.text()
    assert not page.sample_next.isEnabled()
    table.cell_item(2, COL_NAME).setText("样品三")
    table.cell_item(2, COL_SPIKE).setText("0.25")
    assert not page.sample_problem.isVisibleTo(page) and page.sample_next.isEnabled()
    assert page.draft().rows[2].name == "样品三" and page.draft().rows[2].spiked_endotoxin == 0.25
    assert page._draft.rows[2].batch_no == ""


def test_going_back_keeps_everything_that_was_entered(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_wizard(qapp, window)
    reach_samples(qapp, page)
    page.sample_table.cell_item(1, COL_NAME).setText("样品乙")
    page.sample_table.cell_item(1, COL_BATCH).setText("B-7")
    select_tubes(qapp, page, range(20, 37))
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
    assert page.plate.selected_tubes() == list(range(20, 37))
    assert page.sample_table.cell_text(1, COL_NAME) == "样品乙"
    assert page.sample_table.cell_text(1, COL_BATCH) == "B-7"
    assert page.sample_table.cell_text(0, COL_TUBE) == "20"
    click(qapp, page.sample_next)
    click(qapp, page.confirm_back)
    assert page.current_step() == STEP_SAMPLES and page.draft().selected == list(range(20, 37))


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
    assert page.draft().template.name == "限度试验（复试）" and page.draft().selected == []  # type: ignore[union-attr]
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
    assert window.current_route == "new" and asked == ["离开新试验向导？"]
    assert page.draft().selected == list(range(1, 9)) and page.current_step() == STEP_SAMPLES
    # 快捷栏和侧栏走同一个入口
    window.shell.toolbar.actionTriggered.emit("templates")
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


def test_the_experimenter_can_open_the_wizard_but_the_analyst_and_admin_cannot(
    qapp: QApplication, window: MainWindow
) -> None:
    for username, allowed in (("experimenter", True), ("analyst", False), ("admin", False)):
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
    from gelinspect.ui.pages.new_experiment import NewExperimentPage as Page

    sign_in(qapp, window, "analyst")
    page = Page(window.services, window.bridge)
    page.set_session(window.session)
    assert not page.can_start()
    assert not page.reagent_cards[ReagentKind.WATER].input.isEnabled()
    assert not page.confirm_start.isEnabled()
    assert page.confirm_start.toolTip() == "当前角色无此权限"
    page.deleteLater()
