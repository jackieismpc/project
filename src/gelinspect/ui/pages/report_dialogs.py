"""报告中心的对话框：签署（再次输入密码）和批量导出。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gelinspect.interfaces.storage import ReportRecord, StorageBusyError
from gelinspect.services.auth import AuthError
from gelinspect.services.errors import ServiceError
from gelinspect.services.reporting.report_service import (
    ExportResult,
    SigningNotAllowedError,
)
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
)
from gelinspect.ui.report_labels import describe_signing_blocks
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label

ErrorFactory = Callable[[], str]


def _error_text(error: Exception) -> str:
    """把签署、导出时可能出现的异常说成当前语言的话。"""
    if isinstance(error, AuthError):
        return describe_auth_error(error)
    if isinstance(error, SigningNotAllowedError):
        return "\n".join(describe_signing_blocks(error.blocks, error.conclusion))
    if isinstance(error, ServiceError):
        return describe_service_error(error)
    if isinstance(error, StorageBusyError):
        return describe_storage_busy()
    raise error


class SignDialog(QDialog):
    """签署对话框：再次输入密码确认身份。

    密码只在内存里传给服务层，不保存、不写日志。失败（密码错误、有待复核等）时对话框保持打开，
    显示原因；成功后关闭。
    """

    def __init__(
        self,
        apply: Callable[[str], None],
        *,
        subject: str,
        signer: str,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            apply: 执行签署的函数，参数是密码；失败时抛出服务层的异常。
            subject: 要签署的对象，例如试验标识或批号。
            signer: 签署人（当前登录用户的显示名）。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._apply = apply
        self._subject = subject
        self._signer = signer
        self._error_factory: ErrorFactory | None = None
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(6)
        self._title = Label("", "h2")
        self._intro = Label("", "body")
        self._intro.setWordWrap(True)
        self._note = Label("", "meta")
        self._note.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        self._password_label = Label("", "lab")
        self._password_label.setContentsMargins(0, 8, 0, 0)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(current_tokens().space.touch)
        layout.addWidget(self._password_label)
        layout.addWidget(self.password)
        layout.addWidget(self._note)
        self.error_label = Label("", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = Button("", ButtonVariant.SECONDARY)
        self.ok_button = Button("", ButtonVariant.PRIMARY)
        for button in (self.cancel_button, self.ok_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.cancel_button.setAutoDefault(False)
        self.ok_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.retranslate()

    def _submit(self) -> None:
        password = self.password.text()
        self.ok_button.setEnabled(False)
        try:
            self._apply(password)
        except (AuthError, ServiceError, StorageBusyError) as error:
            self.password.clear()
            self._show(partial(_error_text, error))
            self.ok_button.setEnabled(True)
            return
        self.password.clear()
        self.accept()

    def _show(self, factory: ErrorFactory) -> None:
        self._error_factory = factory
        self.error_label.setText(factory())
        self.error_label.setVisible(True)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("签署报告"))
        self._title.setText(self.tr("签署报告"))
        self._intro.setText(
            self.tr("{signer} 将签署 {subject}。请再次输入你的登录密码确认身份。").format(
                signer=self._signer, subject=self._subject
            )
        )
        self._password_label.setText(self.tr("登录密码"))
        self._note.setText(
            self.tr("签署后试验的结果和复核将被锁定，生成带数字签名的 PDF，不能撤销。")
        )
        self.cancel_button.setText(self.tr("取消"))
        self.ok_button.setText(self.tr("签署"))
        for button in (self.cancel_button, self.ok_button):
            button.setAccessibleName(button.text())
        if self._error_factory is not None:
            self.error_label.setText(self._error_factory())

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class BatchExportDialog(QDialog):
    """批量导出对话框：勾选已签署的报告，选目录，导出 PDF 和清单 CSV。"""

    def __init__(
        self,
        records: Sequence[ReportRecord],
        export: Callable[[Sequence[str], str], ExportResult],
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            records: 可导出的已签署报告。
            export: 执行导出的函数 ``(报告标识列表, 目录)``，失败时抛出服务层的异常。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._records = list(records)
        self._export = export
        self._result: ExportResult | None = None
        self._error_factory: ErrorFactory | None = None
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        self._title = Label("", "h2")
        self._intro = Label("", "body")
        self._intro.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        self.list = QListWidget()
        self.list.setMinimumHeight(180)
        for record in self._records:
            item = QListWidgetItem(self._label_of(record))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, record.report_id)
            self.list.addItem(item)
        layout.addWidget(self.list)
        select = QHBoxLayout()
        self.all_button = Button("", ButtonVariant.SECONDARY)
        self.none_button = Button("", ButtonVariant.SECONDARY)
        select.addWidget(self.all_button)
        select.addWidget(self.none_button)
        select.addStretch(1)
        layout.addLayout(select)
        self._directory_label = Label("", "lab")
        layout.addWidget(self._directory_label)
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setMinimumHeight(current_tokens().space.touch)
        self.browse_button = Button("", ButtonVariant.SECONDARY)
        row.addWidget(self.directory, 1)
        row.addWidget(self.browse_button)
        layout.addLayout(row)
        self.result_label = Label("", "body")
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = Button("", ButtonVariant.SECONDARY)
        self.export_button = Button("", ButtonVariant.PRIMARY)
        for button in (self.close_button, self.export_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        self.export_button.clicked.connect(self._run)
        self.browse_button.clicked.connect(self._browse)
        self.all_button.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        self.none_button.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        self.retranslate()

    @staticmethod
    def _label_of(record: ReportRecord) -> str:
        what = record.experiment_id or f"{record.batch_no}"
        when = (
            ""
            if record.signed_at is None
            else record.signed_at.astimezone().strftime("%Y-%m-%d %H:%M")
        )
        return f"{what}  ·  {record.signed_by or ''}  ·  {when}"

    @property
    def export_result(self) -> ExportResult | None:
        """最近一次导出的结果，还没有导出时为 None。"""
        return self._result

    def selected_ids(self) -> list[str]:
        """勾选的报告标识。

        Returns:
            报告标识列表。
        """
        ids: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _check_all(self, state: Qt.CheckState) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item is not None:
                item.setCheckState(state)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, self.tr("选择导出目录"))
        if chosen:
            self.directory.setText(chosen)

    def _run(self) -> None:
        directory = self.directory.text().strip()
        ids = self.selected_ids()
        if not ids:
            self._show(lambda: self.tr("请至少勾选一份报告。"), error=True)
            return
        if not directory:
            self._show(lambda: self.tr("请先选择导出目录。"), error=True)
            return
        try:
            self._result = self._export(ids, directory)
        except (AuthError, ServiceError, StorageBusyError) as error:
            self._show(partial(_error_text, error), error=True)
            return
        self._show(self._summary, error=bool(self._result.failures))

    def _summary(self) -> str:
        result = self._result
        if result is None:
            return ""
        text = self.tr("已导出 {n} 份到 {dir}，用时 {s:.2f} 秒。清单：{manifest}").format(
            n=result.exported,
            dir=str(result.directory),
            s=result.elapsed_s,
            manifest=result.manifest.name,
        )
        if result.failures:
            reasons = {
                "missing": self.tr("文件不存在"),
                "hash_mismatch": self.tr("与入库的哈希不符（可能被改动）"),
                "unsigned": self.tr("没有签名"),
                "not_found": self.tr("没有这条记录"),
            }
            text += "\n" + self.tr("有 {n} 份没有导出：").format(n=len(result.failures))
            text += "；".join(
                f"{failure.report_id[:8]} {reasons.get(failure.reason, failure.reason)}"
                for failure in result.failures
            )
        return text

    def _show(self, factory: ErrorFactory, *, error: bool) -> None:
        self._error_factory = factory
        self.result_label.set_role("danger" if error else "ok")
        self.result_label.setText(factory())
        self.result_label.setVisible(True)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("批量导出报告"))
        self._title.setText(self.tr("批量导出报告"))
        self._intro.setText(
            self.tr(
                "把勾选的已签署报告复制到一个目录，并写一份清单 CSV（文件名、SHA-256、"
                "签署人和时间）。文件与库里的哈希不符时不导出。"
            )
        )
        self.all_button.setText(self.tr("全选"))
        self.none_button.setText(self.tr("全不选"))
        self._directory_label.setText(self.tr("导出目录"))
        self.browse_button.setText(self.tr("浏览…"))
        self.close_button.setText(self.tr("关闭"))
        self.export_button.setText(self.tr("导出"))
        for button in (self.close_button, self.export_button):
            button.setAccessibleName(button.text())
        if self._error_factory is not None:
            self.result_label.setText(self._error_factory())

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
