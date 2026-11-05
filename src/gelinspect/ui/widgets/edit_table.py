"""可编辑表格：自绘单元格，样式与 ``DataTable`` 一致（无网格、行分隔线、等宽数字）。

用于向导的样品表和模板编辑器。列有四种：只读、文字、数字（可留空）和选项。
内容保存在单元格里，调用方通过 :meth:`EditTable.cell_text` / :meth:`EditTable.cell_choice` 读取。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDoubleValidator, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from gelinspect.ui.theme.fonts import mono_font, sans_font
from gelinspect.ui.theme.tokens import current_tokens

ROW_HEIGHT = 38
"""数据行高（px）。"""

_Index = QModelIndex | QPersistentModelIndex
_CHOICE_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True, slots=True)
class EditColumn:
    """可编辑表格的列定义。

    Attributes:
        title: 表头文字。
        kind: ``readonly``、``text``、``number`` 或 ``choice``。
        mono: 是否用等宽字体。
        placeholder: 文字列为空时显示的提示（不是内容）。
        choices: 选项列的 ``(值, 显示文字)``。
        max_length: 文字列的最大长度。
    """

    title: str
    kind: str = "text"
    mono: bool = False
    placeholder: str = ""
    choices: Sequence[tuple[str, str]] = field(default_factory=tuple)
    max_length: int = 128


class _Delegate(QStyledItemDelegate):
    """自绘单元格并创建编辑器。"""

    def __init__(self, table: EditTable) -> None:
        super().__init__(table)
        self._table = table

    def _column(self, index: _Index) -> EditColumn:
        return self._table.columns[index.column()]

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:  # noqa: N802
        """单元格尺寸。

        Args:
            option: 样式选项。
            index: 单元格索引。

        Returns:
            按文字宽度加内边距的尺寸。
        """
        column = self._column(index)
        font = mono_font(13) if column.mono else sans_font(13)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or column.placeholder)
        return QSize(QFontMetrics(font).horizontalAdvance(text) + 24, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        """绘制单元格。

        Args:
            painter: 画笔。
            option: 样式选项。
            index: 单元格索引。
        """
        c = current_tokens().colors
        column = self._column(index)
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(c.border_soft), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        color = c.ink
        if not text and column.placeholder:
            text, color = column.placeholder, c.ink_muted
        elif column.kind == "readonly":
            color = c.ink
        painter.setFont(mono_font(13) if column.mono and color == c.ink else sans_font(13))
        painter.setPen(QColor(color))
        painter.drawText(
            QRectF(rect.left() + 10, rect.top(), rect.width() - 20, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def createEditor(  # noqa: N802
        self, parent: QWidget, option: QStyleOptionViewItem, index: _Index
    ) -> QWidget:
        """创建编辑器。

        Args:
            parent: 父控件。
            option: 样式选项。
            index: 单元格索引。

        Returns:
            文字和数字用 ``QLineEdit``，选项用 ``QComboBox``；只读列的单元格不可编辑，
            不会走到这里（兜底返回只读的 ``QLineEdit``）。
        """
        column = self._column(index)
        if column.kind == "choice":
            combo = QComboBox(parent)
            for value, label in column.choices:
                combo.addItem(label, value)
            return combo
        edit = QLineEdit(parent)
        edit.setReadOnly(column.kind == "readonly")
        edit.setMaxLength(column.max_length)
        edit.setAccessibleName(column.title)
        if column.kind == "number":
            validator = QDoubleValidator(0.0, 1.0e9, 6, edit)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            edit.setValidator(validator)
        return edit

    def setEditorData(self, editor: QWidget, index: _Index) -> None:  # noqa: N802
        """把单元格内容放进编辑器。

        Args:
            editor: 编辑器。
            index: 单元格索引。
        """
        if isinstance(editor, QComboBox):
            current = index.data(_CHOICE_ROLE)
            position = editor.findData(current)
            editor.setCurrentIndex(max(0, position))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
            editor.selectAll()

    def setModelData(  # noqa: N802
        self,
        editor: QWidget,
        model: Any,
        index: _Index,  # noqa: ANN401
    ) -> None:
        """把编辑器内容写回单元格。

        Args:
            editor: 编辑器。
            model: 数据模型。
            index: 单元格索引。
        """
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData(), _CHOICE_ROLE)
            model.setData(index, editor.currentText(), Qt.ItemDataRole.DisplayRole)
        elif isinstance(editor, QLineEdit):
            model.setData(index, editor.text().strip(), Qt.ItemDataRole.DisplayRole)

    def updateEditorGeometry(  # noqa: N802
        self, editor: QWidget, option: QStyleOptionViewItem, index: _Index
    ) -> None:
        """让编辑器填满单元格。

        Args:
            editor: 编辑器。
            option: 样式选项。
            index: 单元格索引。
        """
        editor.setGeometry(option.rect.adjusted(2, 1, -2, -1))


class EditTable(QTableWidget):
    """可编辑表格。

    Signals:
        cellEdited: 用户改了某个单元格，参数为行号和列号。
    """

    cellEdited = Signal(int, int)  # noqa: N815

    def __init__(self, columns: Sequence[EditColumn], parent: QWidget | None = None) -> None:
        """创建表格。

        Args:
            columns: 列定义。
            parent: 父控件。
        """
        super().__init__(0, len(columns), parent)
        self.columns = list(columns)
        self.setItemDelegate(_Delegate(self))
        self.setShowGrid(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.AnyKeyPressed
            | QAbstractItemView.EditTrigger.CurrentChanged
        )
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.itemChanged.connect(lambda item: self.cellEdited.emit(item.row(), item.column()))
        self.retitle()

    def retitle(self, titles: Sequence[str] | None = None) -> None:
        """刷新表头文字（语言切换后调用）。

        Args:
            titles: 新的表头文字，默认用列定义里的。
        """
        labels = list(titles) if titles is not None else [c.title for c in self.columns]
        self.setHorizontalHeaderLabels(labels)

    def set_columns(self, columns: Sequence[EditColumn]) -> None:
        """替换列定义（语言切换时刷新表头和提示）。

        Args:
            columns: 新列定义，列数不变。
        """
        self.columns = list(columns)
        self.retitle()
        self.viewport().update()

    def set_rows(
        self, rows: Sequence[Sequence[str]], choices: Sequence[Sequence[str | None]] = ()
    ) -> None:
        """整体替换内容，不触发 ``cellEdited``。

        Args:
            rows: 每行每列的显示文字。
            choices: 与 ``rows`` 同形，选项列的值（其他列为 None）。
        """
        blocked = self.blockSignals(True)
        try:
            self.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for col, text in enumerate(row):
                    self._put(r, col, text, choices[r][col] if choices else None)
        finally:
            self.blockSignals(blocked)
        self.viewport().update()

    def append_row(self, texts: Sequence[str], choice_values: Sequence[str | None] = ()) -> None:
        """追加一行，不触发 ``cellEdited``。

        Args:
            texts: 每列的显示文字。
            choice_values: 选项列的值。
        """
        blocked = self.blockSignals(True)
        try:
            row = self.rowCount()
            self.insertRow(row)
            for col, text in enumerate(texts):
                self._put(row, col, text, choice_values[col] if choice_values else None)
        finally:
            self.blockSignals(blocked)

    def _put(self, row: int, col: int, text: str, choice: str | None) -> None:
        item = self.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.setItem(row, col, item)
        flags = item.flags()
        if self.columns[col].kind == "readonly":
            flags &= ~Qt.ItemFlag.ItemIsEditable
        else:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        item.setText(text)
        item.setData(_CHOICE_ROLE, choice)

    def set_cell_text(self, row: int, col: int, text: str) -> None:
        """设置某个单元格的文字，不触发 ``cellEdited``。

        Args:
            row: 行号。
            col: 列号。
            text: 文字。
        """
        blocked = self.blockSignals(True)
        try:
            self._put(row, col, text, None)
        finally:
            self.blockSignals(blocked)
        self.viewport().update()

    def cell_item(self, row: int, col: int) -> QTableWidgetItem:
        """取单元格对象（测试和需要直接改单元格的调用方用）。

        Args:
            row: 行号。
            col: 列号。

        Returns:
            单元格，行列都在范围内时总是存在。

        Raises:
            IndexError: 行或列超出范围。
        """
        item = self.item(row, col)
        if item is None:
            raise IndexError(f"没有单元格 ({row}, {col})")
        return item

    def cell_text(self, row: int, col: int) -> str:
        """读取单元格文字。

        Args:
            row: 行号。
            col: 列号。

        Returns:
            文字，没有时为空串。
        """
        item = self.item(row, col)
        return item.text() if item is not None else ""

    def cell_choice(self, row: int, col: int) -> str | None:
        """读取选项列的值。

        Args:
            row: 行号。
            col: 列号。

        Returns:
            选项值，没有时为 None。
        """
        item = self.item(row, col)
        value = item.data(_CHOICE_ROLE) if item is not None else None
        return None if value is None else str(value)

    def commit_editor(self) -> None:
        """提交正在编辑的单元格（保存前调用，避免丢失最后一次输入）。"""
        editor = self.focusWidget()
        if isinstance(editor, QLineEdit | QComboBox) and self.isAncestorOf(editor):
            self.commitData(editor)
            self.closeEditor(editor, QStyledItemDelegate.EndEditHint.NoHint)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新表头。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retitle()
