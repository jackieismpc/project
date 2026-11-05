"""模板编辑对话框：名称、类型，以及每支试管的样品名、稀释倍数、外加内毒素和用途。

对话框不调用服务：保存时调用外面传入的函数，函数抛 ``ServiceError`` 时在对话框里显示原因，
不关闭，让用户改完重试。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.plate import TUBE_COUNT
from gelinspect.domain.templates import ExperimentTemplate
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.auth import AuthError
from gelinspect.services.errors import ServiceError
from gelinspect.services.template_service import TemplateDraft, TemplateItemInput
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    experiment_type_label,
    tube_role_label,
)
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.edit_table import EditColumn, EditTable
from gelinspect.ui.widgets.label import Label

COL_NO, COL_SAMPLE, COL_DILUTION, COL_SPIKE, COL_MULTIPLE, COL_ROLE = range(6)
"""编辑表的列。"""

Submit = Callable[[TemplateDraft], ExperimentTemplate]
"""保存函数：成功返回模板，失败抛 ``ServiceError``、``AuthError`` 或 ``StorageBusyError``。"""


def _number(text: str) -> float | None:
    try:
        return float(text) if text.strip() else None
    except ValueError:
        return None


class TemplateEditorDialog(QDialog):
    """新建或编辑自定义模板的对话框。"""

    def __init__(
        self,
        submit: Submit,
        *,
        initial: TemplateDraft | None = None,
        editing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            submit: 保存函数。
            initial: 初始内容（复制或编辑时），默认是一支空试管。
            editing: 是编辑已有模板（影响标题）。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumSize(720, 520)
        self._submit = submit
        self._editing = editing
        self.saved: ExperimentTemplate | None = None
        draft = initial or TemplateDraft(
            "", ExperimentType.SENSITIVITY_CONFIRMATION, [TemplateItemInput("")]
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        self.heading = Label("", "h2")
        layout.addWidget(self.heading)

        form = QHBoxLayout()
        form.setSpacing(12)
        name_col = QVBoxLayout()
        name_col.setSpacing(4)
        self.name_label = Label("", "lab")
        self.name = QLineEdit(draft.name)
        self.name.setMinimumHeight(44)
        self.name.setMaxLength(60)
        name_col.addWidget(self.name_label)
        name_col.addWidget(self.name)
        type_col = QVBoxLayout()
        type_col.setSpacing(4)
        self.type_label = Label("", "lab")
        self.type_box = QComboBox()
        self.type_box.setMinimumHeight(44)
        for kind in ExperimentType:
            self.type_box.addItem("", kind.value)
        self.type_box.setCurrentIndex(self.type_box.findData(draft.experiment_type.value))
        type_col.addWidget(self.type_label)
        type_col.addWidget(self.type_box)
        form.addLayout(name_col, 2)
        form.addLayout(type_col, 1)
        layout.addLayout(form)

        self.table = EditTable(self._columns())
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table, 1)
        for i, item in enumerate(draft.items, start=1):
            self._append(i, item)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.add_button = Button("", ButtonVariant.SECONDARY, icon="plus")
        self.remove_button = Button("", ButtonVariant.SECONDARY)
        self.count_label = Label("", "meta")
        tools.addWidget(self.add_button)
        tools.addWidget(self.remove_button)
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        layout.addLayout(tools)

        self.error_label = Label("", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = Button("", ButtonVariant.SECONDARY)
        self.save_button = Button("", ButtonVariant.PRIMARY)
        for button in (self.cancel_button, self.save_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        self.add_button.clicked.connect(self._add_row)
        self.remove_button.clicked.connect(self._remove_row)
        self.retranslate()

    # ------------------------------------------------------------------ 表格

    def _columns(self) -> list[EditColumn]:
        return [
            EditColumn(self.tr("管号"), "readonly", mono=True),
            EditColumn(self.tr("样品名称"), "text"),
            EditColumn(self.tr("稀释倍数"), "number", mono=True),
            EditColumn(self.tr("外加内毒素"), "number", mono=True),
            EditColumn(self.tr("λ 倍数"), "number", mono=True),
            EditColumn(
                self.tr("用途"),
                "choice",
                choices=[(role.value, tube_role_label(role)) for role in TubeRole],
            ),
        ]

    def _append(self, number: int, item: TemplateItemInput) -> None:
        self.table.append_row(
            [
                str(number),
                item.sample_name,
                "" if item.dilution_factor is None else f"{item.dilution_factor:g}",
                "" if item.spiked_endotoxin is None else f"{item.spiked_endotoxin:g}",
                "" if item.lambda_multiple is None else f"{item.lambda_multiple:g}",
                tube_role_label(item.role),
            ],
            [None, None, None, None, None, item.role.value],
        )

    def _add_row(self) -> None:
        if self.table.rowCount() >= TUBE_COUNT:
            return
        self._append(self.table.rowCount() + 1, TemplateItemInput(""))
        self.table.scrollToBottom()
        self._refresh_count()

    def _remove_row(self) -> None:
        self.table.commit_editor()
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.rowCount() - 1)
        self._refresh_count()

    def _refresh_count(self) -> None:
        count = self.table.rowCount()
        self.count_label.setText(self.tr("共 {count} 支试管").format(count=count))
        self.add_button.setEnabled(count < TUBE_COUNT)
        self.remove_button.setEnabled(count > 1)

    def draft(self) -> TemplateDraft:
        """读取对话框里当前的内容。

        Returns:
            模板输入，管号按行顺序从 1 编。
        """
        self.table.commit_editor()
        items = [
            TemplateItemInput(
                sample_name=self.table.cell_text(r, COL_SAMPLE),
                role=TubeRole(self.table.cell_choice(r, COL_ROLE) or TubeRole.TEST.value),
                dilution_factor=_number(self.table.cell_text(r, COL_DILUTION)),
                spiked_endotoxin=_number(self.table.cell_text(r, COL_SPIKE)),
                lambda_multiple=_number(self.table.cell_text(r, COL_MULTIPLE)),
            )
            for r in range(self.table.rowCount())
        ]
        return TemplateDraft(
            name=self.name.text(),
            experiment_type=ExperimentType(str(self.type_box.currentData())),
            items=items,
        )

    # ------------------------------------------------------------------ 保存

    def _save(self) -> None:
        try:
            self.saved = self._submit(self.draft())
        except ServiceError as error:
            self._show_error(describe_service_error(error))
            return
        except AuthError as error:
            self._show_error(describe_auth_error(error))
            return
        except StorageBusyError:
            self._show_error(describe_storage_busy())
            return
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(True)

    # ------------------------------------------------------------------ 文字

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        title = self.tr("编辑模板") if self._editing else self.tr("新建模板")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.heading.setText(title)
        self.name_label.setText(self.tr("模板名称"))
        self.type_label.setText(self.tr("试验类型"))
        for index, kind in enumerate(ExperimentType):
            self.type_box.setItemText(index, experiment_type_label(kind))
        self.table.set_columns(self._columns())
        for r in range(self.table.rowCount()):
            value = self.table.cell_choice(r, COL_ROLE) or TubeRole.TEST.value
            self.table.set_cell_text(r, COL_ROLE, tube_role_label(TubeRole(value)))
            item = self.table.item(r, COL_ROLE)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, value)
        self.add_button.setText(self.tr("添加试管"))
        self.remove_button.setText(self.tr("删除最后一支"))
        self.cancel_button.setText(self.tr("取消"))
        self.save_button.setText(self.tr("保存"))
        self._refresh_count()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
