"""新试验向导第 3 步的 MVD 和内毒素限值：可选录入，非法值挡住下一步，固化到试验记录。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.new_experiment import STEP_CONFIRM, STEP_SAMPLES, NewExperimentPage
from tests.ui.conftest import settle, wait_for
from tests.ui.wizard_helpers import (
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    select_tubes,
)


def to_samples_step(app: QApplication, window: MainWindow) -> NewExperimentPage:
    page = open_wizard(app, window, "experimenter")
    choose_template(app, page, "灵敏度复核 (2λ:1)")
    click(app, page.template_next)
    pass_reagents(app, page)
    click(app, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES
    select_tubes(app, page, range(1, 18))
    return page


def info_rows(page: NewExperimentPage) -> dict[str, str]:
    grid = page.info_grid
    rows: dict[str, str] = {}
    for row in range(grid.rowCount()):
        label, value = grid.itemAtPosition(row, 0), grid.itemAtPosition(row, 1)
        if label is not None and value is not None:
            rows[label.widget().text()] = value.widget().text()  # type: ignore[union-attr]
    return rows


def test_the_fields_are_optional(qapp: QApplication, fast_window: MainWindow) -> None:
    page = to_samples_step(qapp, fast_window)
    assert page.mvd_label.text() == "MVD（可选）" and "可选" in page.limit_label.text()
    assert page.mvd_edit.text() == "" and page.limit_edit.text() == ""
    assert page.sample_next.isEnabled() and not page.limit_problem.isVisibleTo(page)
    click(qapp, page.sample_next)
    assert page.current_step() == STEP_CONFIRM
    assert "MVD" not in info_rows(page)  # 没填就不显示
    assert (
        page.draft().to_request().mvd is None and page.draft().to_request().endotoxin_limit is None
    )


def test_a_bad_value_blocks_the_next_step_and_says_which_field(
    qapp: QApplication, fast_window: MainWindow
) -> None:
    page = to_samples_step(qapp, fast_window)
    page.mvd_edit.setText("abc")
    page.limit_edit.setText("-1")
    settle(qapp)
    assert not page.sample_next.isEnabled() and page.limit_problem.isVisibleTo(page)
    assert "“MVD、内毒素限值”必须是大于 0 的数字" in page.limit_problem.text()
    click(qapp, page.sample_next)
    assert page.current_step() == STEP_SAMPLES  # 直接点也过不去
    page.mvd_edit.setText("1280")
    assert "“内毒素限值”必须是大于 0 的数字" in page.limit_problem.text()
    page.limit_edit.setText("")  # 清空 = 不填，允许
    settle(qapp)
    assert page.sample_next.isEnabled() and not page.limit_problem.isVisibleTo(page)


def test_values_are_shown_on_the_confirm_step_and_stored_with_the_experiment(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    page = to_samples_step(qapp, fast_window)
    page.mvd_edit.setText("1280")
    page.limit_edit.setText("0.25")
    click(qapp, page.sample_next)
    rows = info_rows(page)
    assert rows["MVD"] == "1280" and rows["内毒素限值"] == "0.25 EU/mL"
    click(qapp, page.sample_back)  # 返回不丢内容
    assert page.mvd_edit.text() == "1280" and page.limit_edit.text() == "0.25"
    click(qapp, page.sample_next)
    click(qapp, page.confirm_start)
    assert wait_for(qapp, lambda: fast_window.current_route == "monitor", 30)
    monitor = fast_window.page("monitor")
    experiment_id = monitor.experiment_id  # type: ignore[union-attr]
    stored = fast_context.database.experiments.get(experiment_id)
    assert stored is not None and stored.mvd == 1280.0 and stored.endotoxin_limit == 0.25
    assert wait_for(
        qapp,
        lambda: (
            (e := fast_context.database.experiments.get(experiment_id)) is not None
            and e.state in (ExperimentState.PENDING_REVIEW, ExperimentState.COMPLETED)
        ),
        60,
    )
    # 向导重置后输入框是空的
    fast_window.navigate("new")
    new_page = fast_window.page("new")
    assert isinstance(new_page, NewExperimentPage)
    assert new_page.mvd_edit.text() == "" and new_page.limit_edit.text() == ""
