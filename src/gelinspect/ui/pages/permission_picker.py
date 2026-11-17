"""普通用户的六组授权选择，界面只呈现业务组而非底层权限。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent
from PySide6.QtWidgets import QCheckBox, QDialog, QGridLayout, QVBoxLayout, QWidget

from gelinspect.domain.permissions import PermissionGroup
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.auth import AuthError
from gelinspect.services.errors import ServiceError
from gelinspect.ui.pages.users_dialogs import _problem_text
from gelinspect.ui.widgets.button import Button
from gelinspect.ui.widgets.label import Label

_NAMES: dict[PermissionGroup, str] = {
    PermissionGroup.VIEW: str(QT_TRANSLATE_NOOP("PermissionGroup", "查看记录")),
    PermissionGroup.EXPERIMENT: str(QT_TRANSLATE_NOOP("PermissionGroup", "实验与结果处理")),
    PermissionGroup.TEMPLATE: str(QT_TRANSLATE_NOOP("PermissionGroup", "模板管理")),
    PermissionGroup.OUTPUT: str(QT_TRANSLATE_NOOP("PermissionGroup", "打印与导出")),
    PermissionGroup.AUDIT: str(QT_TRANSLATE_NOOP("PermissionGroup", "审计查询")),
    PermissionGroup.MAINTENANCE: str(QT_TRANSLATE_NOOP("PermissionGroup", "仪器与维护")),
}


def group_label(group: PermissionGroup) -> str:
    """返回授权组的界面名称。"""
    return QCoreApplication.translate("PermissionGroup", _NAMES[group])


class PermissionPicker(QWidget):
    """六个可组合勾选项，默认不选。"""

    def __init__(
        self, groups: frozenset[PermissionGroup] = frozenset(), parent: QWidget | None = None
    ) -> None:
        """创建授权选择区。"""
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.boxes = {g: QCheckBox(group_label(g)) for g in PermissionGroup}
        for index, (group, box) in enumerate(self.boxes.items()):
            box.setChecked(group in groups)
            layout.addWidget(box, index // 2, index % 2)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """切换语言时保留选择，刷新组名称。"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            for group, box in self.boxes.items():
                box.setText(group_label(group))

    def groups(self) -> frozenset[PermissionGroup]:
        """获取当前选中的业务组。"""
        return frozenset(g for g, box in self.boxes.items() if box.isChecked())


class PermissionDialog(QDialog):
    """调整普通用户授权，保存失败保留选择。"""

    def __init__(
        self,
        username: str,
        groups: frozenset[PermissionGroup],
        submit: Callable[[frozenset[PermissionGroup]], object],
        parent: QWidget | None = None,
    ) -> None:
        """创建用户授权对话框。"""
        super().__init__(parent)
        self.setWindowTitle(self.tr("用户权限"))
        self.setMinimumWidth(420)
        self.setProperty("role", "dialog")
        self._submit = submit
        layout = QVBoxLayout(self)
        layout.addWidget(Label(username, "val"))
        self.picker = PermissionPicker(groups)
        layout.addWidget(self.picker)
        note = Label(
            self.tr("可组合授权；业务权限包含必要的查看能力。未选择任何项则无业务权限。"), "meta"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = Label("", "danger")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.save_button = Button(self.tr("保存"))
        self.cancel_button = Button(self.tr("取消"))
        self.save_button.clicked.connect(self.submit)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.save_button)
        layout.addWidget(self.cancel_button)

    def submit(self) -> None:
        """提交整组授权并保留业务失败提示。"""
        try:
            self._submit(self.picker.groups())
        except (AuthError, ServiceError, StorageBusyError) as error:
            self.error.setText(_problem_text(error))
            self.error.show()
            return
        self.accept()
