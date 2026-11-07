"""模板管理页：模板列表和管位详情；管理员可以新建、复制、编辑自定义模板。

所有角色可以查看（``template_view``）；新建、复制、编辑需要 ``template_manage``，其他角色看到
禁用的按钮和“只读”标记。内置模板只读，只能复制。权限在服务层再校验一次。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.template_service import TemplateInfo, draft_of
from gelinspect.ui.labels import (
    builtin_name_text,
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    experiment_type_label,
    tube_role_label,
)
from gelinspect.ui.pages.template_editor import TemplateEditorDialog
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


class TemplatesPage(QWidget):
    """模板管理页。"""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._infos: list[TemplateInfo] = []
        self._selected_id: str | None = None
        self._error = ""

        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("role", "root")
        scroll.setWidget(page)
        column = QVBoxLayout(page)
        column.setContentsMargins(24, 20, 24, 28)
        column.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.readonly_badge = StatusBadge("", BadgeTone.NEUTRAL)
        self.new_button = Button("", ButtonVariant.PRIMARY, icon="plus")
        self.copy_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        self.edit_button = Button("", ButtonVariant.SECONDARY, icon="pencil")
        top.addWidget(self.title)
        top.addWidget(self.readonly_badge)
        top.addStretch(1)
        for button in (self.new_button, self.copy_button, self.edit_button):
            top.addWidget(button)
        column.addLayout(top)

        self.note = Label("", "meta")
        self.note.setWordWrap(True)
        column.addWidget(self.note)

        work = QHBoxLayout()
        work.setSpacing(14)
        self.list_card = Card(" ")
        self.list_table = DataTable(self._list_columns())
        self.list_table.setMinimumHeight(260)
        self.list_card.body.addWidget(self.list_table)
        work.addWidget(self.list_card, 1)
        self.detail_card = Card(" ")
        self.detail_badge = StatusBadge("", BadgeTone.WARNING)
        self.detail_badge.setVisible(False)
        assert self.detail_card.header is not None
        self.detail_card.header.addWidget(self.detail_badge)
        self.detail_table = DataTable(self._detail_columns())
        self.detail_table.setMinimumHeight(260)
        self.detail_card.body.addWidget(self.detail_table)
        work.addWidget(self.detail_card, 1)
        column.addLayout(work, 1)

        self.list_table.view.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self.new_button.clicked.connect(self.create_template)
        self.copy_button.clicked.connect(self.copy_selected)
        self.edit_button.clicked.connect(self.edit_selected)
        self.retranslate()

    # ------------------------------------------------------------------ 列定义

    def _list_columns(self) -> list[Column]:
        return [
            Column("name", self.tr("名称")),
            Column("type", self.tr("类型")),
            Column("count", self.tr("管数"), mono=True, align_right=True),
            Column("source", self.tr("来源")),
            Column("state", self.tr("状态")),
        ]

    def _detail_columns(self) -> list[Column]:
        return [
            Column("no", self.tr("管号"), mono=True),
            Column("sample", self.tr("样品名称")),
            Column("role", self.tr("用途")),
            Column("dilution", self.tr("稀释倍数"), mono=True),
            Column("spike", self.tr("外加内毒素"), mono=True),
        ]

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话，退出登录时为 None。
        """
        self._session = session
        self.refresh()

    def refresh(self, select: str | None = None) -> None:
        """重新读取模板。

        Args:
            select: 刷新后选中的模板标识，默认保持当前选中。
        """
        self._error = ""
        try:
            self._infos = self._services.templates.list_templates(self._session)
        except AuthError as error:
            self._infos = []
            self._error = describe_auth_error(error)
        except StorageBusyError:
            self._infos = []
            self._error = describe_storage_busy()
        wanted = select or self._selected_id
        if all(i.template.template_id != wanted for i in self._infos):
            wanted = self._infos[0].template.template_id if self._infos else None
        self._selected_id = wanted
        self._render()

    def selected(self) -> TemplateInfo | None:
        """当前选中的模板。

        Returns:
            模板，没有选中时为 None。
        """
        return next((i for i in self._infos if i.template.template_id == self._selected_id), None)

    def can_manage(self) -> bool:
        """当前会话是否有 ``template_manage`` 权限。

        Returns:
            有权限时为 True。
        """
        return self._session is not None and self._session.can(Permission.TEMPLATE_MANAGE)

    def _on_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid() or current.row() >= len(self._infos):
            return
        self._selected_id = self._infos[current.row()].template.template_id
        self._render_detail()
        self._render_buttons()

    # ------------------------------------------------------------------ 操作

    def create_template(self) -> None:
        """打开编辑对话框新建模板。"""
        dialog = TemplateEditorDialog(
            lambda draft: self._services.templates.create_template(self._session, draft),
            parent=self,
        )
        self._finish(dialog, self.tr("模板已创建"))

    def copy_selected(self) -> None:
        """复制选中的模板（内置或自定义）为新的自定义模板，复制后可以再编辑改名。"""
        info = self.selected()
        if info is None:
            return
        try:
            copy = self._services.templates.copy_template(self._session, info.template.template_id)
        except ServiceError as error:
            self._toast(describe_service_error(error), ToastKind.DANGER)
        except AuthError as error:
            self._toast(describe_auth_error(error), ToastKind.DANGER)
        except StorageBusyError:
            self._toast(describe_storage_busy(), ToastKind.DANGER)
        else:
            self.refresh(select=copy.template_id)
            self._toast(self.tr("模板已复制，可以点“编辑”修改"), ToastKind.SUCCESS)

    def edit_selected(self) -> None:
        """编辑选中的自定义模板。"""
        info = self.selected()
        if info is None or info.builtin:
            return
        template_id = info.template.template_id
        dialog = TemplateEditorDialog(
            lambda draft: self._services.templates.update_template(
                self._session, template_id, draft
            ),
            initial=draft_of(info.template),
            editing=True,
            parent=self,
        )
        self._finish(dialog, self.tr("模板已保存"))

    def _finish(self, dialog: TemplateEditorDialog, message: str) -> None:
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved is not None:
            self.refresh(select=dialog.saved.template_id)
            self._toast(message, ToastKind.SUCCESS)

    def _toast(self, message: str, kind: ToastKind) -> None:
        Toast.show_message(self.window(), message, kind)

    # ------------------------------------------------------------------ 显示

    def _render(self) -> None:
        self.list_table.set_rows(
            [
                {
                    "name": builtin_name_text(i.template.name),
                    "type": experiment_type_label(i.template.experiment_type),
                    "count": i.template.tube_count,
                    "source": self.tr("内置") if i.builtin else self.tr("自定义"),
                    "state": self.tr("布局待确认") if i.template.needs_confirmation else "",
                }
                for i in self._infos
            ]
        )
        self.list_table.set_empty_text(self._error or self.tr("还没有模板"))
        model = self.list_table.view.selectionModel()
        for row, info in enumerate(self._infos):
            if info.template.template_id == self._selected_id:
                index = self.list_table.model.index(row, 0)
                model.setCurrentIndex(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self._render_detail()
        self._render_buttons()
        self.readonly_badge.setVisible(not self.can_manage())

    def _render_detail(self) -> None:
        info = self.selected()
        if info is None:
            self.detail_card.set_title(self.tr("管位详情"))
            self.detail_table.set_rows([])
            self.detail_badge.setVisible(False)
            return
        template = info.template
        self.detail_card.set_title(
            self.tr("管位详情 · {name}").format(name=builtin_name_text(template.name))
        )
        self.detail_badge.set_text(self.tr("布局待确认"))
        self.detail_badge.setVisible(template.needs_confirmation)
        self.detail_table.set_rows(
            [
                {
                    "no": item.tube_no,
                    "sample": builtin_name_text(item.sample_name),
                    "role": tube_role_label(item.role),
                    "dilution": _number(item.dilution_factor),
                    "spike": (
                        _number(item.spiked_endotoxin)
                        if item.lambda_multiple is None
                        else f"{item.lambda_multiple:g}λ"
                    ),
                }
                for item in sorted(template.items, key=lambda i: i.tube_no)
            ]
        )

    def _render_buttons(self) -> None:
        manage = self.can_manage()
        info = self.selected()
        denied = self.tr("当前角色无此权限")
        self.new_button.setEnabled(manage)
        self.new_button.setToolTip("" if manage else denied)
        self.copy_button.setEnabled(manage and info is not None)
        self.copy_button.setToolTip("" if manage else denied)
        editable = manage and info is not None and not info.builtin
        self.edit_button.setEnabled(editable)
        if not manage:
            self.edit_button.setToolTip(denied)
        elif info is not None and info.builtin:
            self.edit_button.setToolTip(self.tr("内置模板只读，请先复制再编辑"))
        else:
            self.edit_button.setToolTip("")
        if manage:
            self.note.setText(
                self.tr("内置模板只读，可以复制后修改。自定义模板保存后，新试验向导里立即可选。")
            )
        else:
            self.note.setText(self.tr("当前角色只能查看模板，新建、复制和编辑需要管理员。"))

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("模板管理"))
        self.readonly_badge.set_text(self.tr("只读"))
        self.new_button.setText(self.tr("新建模板"))
        self.copy_button.setText(self.tr("复制"))
        self.edit_button.setText(self.tr("编辑"))
        self.list_card.set_title(self.tr("检测模板"))
        self.list_table.set_columns(self._list_columns())
        self.detail_table.set_columns(self._detail_columns())
        self.detail_table.set_empty_text(self.tr("没有选中的模板"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
