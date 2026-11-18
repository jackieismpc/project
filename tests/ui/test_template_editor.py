"""模板编辑器保留稀疏的物理管号。"""

from pytestqt.qtbot import QtBot

from gelinspect.domain.enums import ExperimentType
from gelinspect.services.template_service import (
    TemplateDraft,
    TemplateItemInput,
    build_template_config,
)
from gelinspect.ui.pages.template_editor import COL_NO, COL_SAMPLE, TemplateEditorDialog


def test_sparse_tube_numbers_survive_edit_delete_and_add(qtbot: QtBot) -> None:
    initial = TemplateDraft(
        "快照模板",
        ExperimentType.LIMIT,
        (TemplateItemInput("A"), TemplateItemInput("B")),
        tube_numbers=(17, 25),
    )
    dialog = TemplateEditorDialog(
        lambda draft: build_template_config("test-template", draft), initial=initial
    )
    qtbot.addWidget(dialog)

    assert [dialog.table.cell_text(row, COL_NO) for row in range(2)] == ["17", "25"]
    assert dialog.draft().tube_numbers == (17, 25)

    dialog.table.selectRow(0)
    dialog._remove_row()
    assert dialog.table.cell_text(0, COL_SAMPLE) == "B"
    assert dialog.draft().tube_numbers == (25,)

    dialog._add_row()
    result = dialog.draft()
    assert result.tube_numbers == (25, 26)
    assert [dialog.table.cell_text(row, COL_NO) for row in range(2)] == ["25", "26"]
