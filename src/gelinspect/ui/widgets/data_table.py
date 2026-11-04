"""数据表格：QTableView 加自绘单元格，空数据时显示空状态。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QStackedLayout,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from gelinspect.ui.theme.fonts import mono_font, sans_font
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.marks import STATE_LABELS, WellState, paint_well

ROW_HEIGHT = 38
"""数据行高（px）。"""

_Index = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True, slots=True)
class Column:
    """列定义。

    Attributes:
        key: 行字典中的键。
        title: 表头文字。
        mono: 是否用等宽字体（管号、批号、时间等）。
        kind: ``text`` 为普通文字；``state`` 的值是 :class:`WellState`，显示标记加文字。
        align_right: 是否右对齐。
    """

    key: str
    title: str
    mono: bool = False
    kind: str = "text"
    align_right: bool = False


class RecordTableModel(QAbstractTableModel):
    """行字典列表的只读表格模型。"""

    def __init__(self, columns: Sequence[Column] = (), parent: QWidget | None = None) -> None:
        """创建模型。

        Args:
            columns: 列定义。
            parent: 父对象。
        """
        super().__init__(parent)
        self.columns: list[Column] = list(columns)
        self.rows: list[Mapping[str, object]] = []

    def set_columns(self, columns: Sequence[Column]) -> None:
        """替换列定义。

        Args:
            columns: 新列定义。
        """
        self.beginResetModel()
        self.columns = list(columns)
        self.endResetModel()

    def set_rows(self, rows: Sequence[Mapping[str, object]]) -> None:
        """替换全部数据行。

        Args:
            rows: 行字典序列。
        """
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008
        """行数。

        Args:
            parent: 父索引（平面模型忽略）。

        Returns:
            数据行数。
        """
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008
        """列数。

        Args:
            parent: 父索引（平面模型忽略）。

        Returns:
            列数。
        """
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: ANN401
        """单元格数据。

        Args:
            index: 单元格索引。
            role: 数据角色。

        Returns:
            显示文字、原始值或对齐方式。
        """
        if not index.isValid():
            return None
        column = self.columns[index.column()]
        value = self.rows[index.row()].get(column.key, "")
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            if isinstance(value, WellState):
                return STATE_LABELS[value]
            return "–" if value in ("", None) else str(value)
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignRight if column.align_right else Qt.AlignmentFlag.AlignLeft
            )
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # noqa: ANN401
        """表头文字。

        Args:
            section: 列序号。
            orientation: 方向。
            role: 数据角色。

        Returns:
            横向表头返回列标题。
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        return None


class _CellDelegate(QStyledItemDelegate):
    """自绘单元格：文字、状态标记与行分隔线。"""

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:  # noqa: N802
        """单元格尺寸。

        Args:
            option: 样式选项。
            index: 单元格索引。

        Returns:
            按文字宽度加内边距的尺寸。
        """
        model = index.model()
        assert isinstance(model, RecordTableModel)
        column = model.columns[index.column()]
        font = mono_font(13) if column.mono else sans_font(13)
        from PySide6.QtGui import QFontMetrics

        width = QFontMetrics(font).horizontalAdvance(str(index.data())) + 20
        if column.kind == "state":
            width += 20
        return QSize(width, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        """绘制单元格。

        Args:
            painter: 画笔。
            option: 样式选项。
            index: 单元格索引。
        """
        model = index.model()
        assert isinstance(model, RecordTableModel)
        tokens = current_tokens()
        c = tokens.colors
        column = model.columns[index.column()]
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if option.state & option.state.__class__.State_Selected:
            painter.fillRect(rect, QColor(c.primary_soft))
        painter.setPen(QPen(QColor(c.border_soft), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        text_left = rect.left() + 10
        value = index.data(Qt.ItemDataRole.UserRole)
        if column.kind == "state" and isinstance(value, WellState):
            paint_well(painter, QRectF(text_left, rect.center().y() - 6, 12, 12), value, tokens)
            text_left += 18
            font = sans_font(13, weight=sans_font(13).Weight.Medium)
        else:
            font = mono_font(13) if column.mono else sans_font(13)
        painter.setFont(font)
        painter.setPen(QColor(c.ink))
        flags = int(
            (Qt.AlignmentFlag.AlignRight if column.align_right else Qt.AlignmentFlag.AlignLeft)
            | Qt.AlignmentFlag.AlignVCenter
        )
        painter.drawText(
            QRectF(text_left, rect.top(), rect.right() - 10 - text_left, rect.height()),
            flags,
            str(index.data()),
        )
        painter.restore()


class DataTable(QWidget):
    """数据表格组件。

    没有数据时显示空状态（图标、标题、提示），而不是只有表头的空表。

    Signals:
        rowActivated: 双击或按回车激活某行，参数为行号。
    """

    rowActivated = Signal(int)  # noqa: N815

    def __init__(
        self,
        columns: Sequence[Column] = (),
        *,
        empty_title: str = "暂无数据",
        empty_hint: str = "",
        fit_content: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建表格。

        Args:
            columns: 列定义。
            empty_title: 空状态标题。
            empty_hint: 空状态提示。
            fit_content: 为 True 时表格高度随行数增长，不出现内部滚动条。
            parent: 父控件。
        """
        super().__init__(parent)
        self._fit = fit_content
        self.model = RecordTableModel(columns, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(_CellDelegate(self.view))
        self.view.setShowGrid(False)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.view.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        if fit_content:
            self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.activated.connect(lambda idx: self.rowActivated.emit(idx.row()))

        self._empty = QFrame()
        self._empty.setProperty("role", "empty")
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(6)
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("inbox", 32, "ink_muted"))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title = Label(empty_title, "val")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint = Label(empty_hint, "meta")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setVisible(bool(empty_hint))
        for w in (icon, self._empty_title, self._empty_hint):
            empty_layout.addWidget(w)
        self._empty.setMinimumHeight(150)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.view)
        self._stack.addWidget(self._empty)
        self._refresh()

    def set_columns(self, columns: Sequence[Column]) -> None:
        """替换列定义。

        Args:
            columns: 新列定义。
        """
        self.model.set_columns(columns)
        self._refresh()

    def set_rows(self, rows: Sequence[Mapping[str, object]]) -> None:
        """替换全部数据行。

        Args:
            rows: 行字典序列。
        """
        self.model.set_rows(rows)
        self._refresh()

    def row_count(self) -> int:
        """返回数据行数。

        Returns:
            行数。
        """
        return self.model.rowCount()

    def is_empty_state_visible(self) -> bool:
        """当前是否显示空状态。

        Returns:
            没有数据行时为 ``True``。
        """
        return self._stack.currentWidget() is self._empty

    def set_empty_text(self, title: str, hint: str = "") -> None:
        """修改空状态文案。

        Args:
            title: 标题。
            hint: 提示，空串则隐藏。
        """
        self._empty_title.setText(title)
        self._empty_hint.setText(hint)
        self._empty_hint.setVisible(bool(hint))

    def _refresh(self) -> None:
        """按行数切换表格与空状态，并在需要时调整高度。"""
        self._stack.setCurrentWidget(self._empty if self.model.rowCount() == 0 else self.view)
        if self._fit and self.model.rowCount() > 0:
            height = self.view.horizontalHeader().height() or 36
            self.view.setFixedHeight(height + self.model.rowCount() * ROW_HEIGHT + 2)
        self.setAccessibleName("数据表格" if self.model.rowCount() else self._empty_title.text())
