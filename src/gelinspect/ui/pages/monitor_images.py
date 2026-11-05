"""监控页的「图像显示」标签：每排一张翻转前、一张翻转后的 8 连图（抓拍原图的缩略图）。

读盘和解码在后台线程里做（12 MP 原图要几百毫秒），完成后在主线程显示；还没有读到的排
先显示「正在读取」。
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.domain.plate import row_label
from gelinspect.services.auth import Session
from gelinspect.services.media_service import ImageWarning, MediaService, RowThumbnails
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.imaging import bgr_to_qimage
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.video_view import VideoView


def warning_text(warnings: tuple[ImageWarning, ...]) -> str:
    """读图告警的当前语言说明。

    Args:
        warnings: 告警。

    Returns:
        多条用分号连接，没有告警为空串。
    """
    texts: list[str] = []
    for warning in warnings:
        if warning is ImageWarning.HASH_MISMATCH:
            texts.append(
                QCoreApplication.translate(
                    "MediaWarning", "图像与入库的 SHA-256 不符，可能已被改动"
                )
            )
        elif warning is ImageWarning.FILE_MISSING:
            texts.append(QCoreApplication.translate("MediaWarning", "部分图像文件缺失或无法解码"))
        elif warning is ImageWarning.NO_MASKS:
            texts.append(
                QCoreApplication.translate("MediaWarning", "没有存档的掩码，按孔位切分显示")
            )
    return "；".join(texts)


class _RowThumb(QWidget):
    def __init__(self, row_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_index = row_index
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        self.title = Label(row_label(row_index), "val", mono=True)
        self.warning = Label("", "danger")
        self.warning.setVisible(False)
        head.addWidget(self.title)
        head.addWidget(self.warning, 1)
        layout.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.before = VideoView(min_height=150, max_height=260)
        self.after = VideoView(min_height=150, max_height=260)
        self.before_caption = Label("", "meta")
        self.after_caption = Label("", "meta")
        grid.addWidget(self.before, 0, 0)
        grid.addWidget(self.after, 0, 1)
        grid.addWidget(self.before_caption, 1, 0)
        grid.addWidget(self.after_caption, 1, 1)
        layout.addLayout(grid)
        self.loaded = False

    def show_thumbnails(self, thumbs: RowThumbnails) -> None:
        self.loaded = True
        for view, image in ((self.before, thumbs.before), (self.after, thumbs.after)):
            view.set_image(None if image is None else bgr_to_qimage(image))
            view.set_text(self.tr("没有这张图"))
        text = warning_text(thumbs.warnings)
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def retranslate(self) -> None:
        self.before_caption.setText(self.tr("翻转前"))
        self.after_caption.setText(self.tr("翻转后"))
        if not self.loaded:
            self.before.set_text(self.tr("正在读取图像…"))
            self.after.set_text(self.tr("正在读取图像…"))


class ImagesTab(Card):
    """图像显示标签。"""

    def __init__(self, media: MediaService, parent: QWidget | None = None) -> None:
        """创建标签页内容。

        Args:
            media: 媒体服务。
            parent: 父控件。
        """
        super().__init__(" ", parent=parent)
        self._media = media
        self._session: Session | None = None
        self._experiment_id: str | None = None
        self._rows: dict[int, _RowThumb] = {}
        self._stale: set[int] = set()
        self._runner = BackgroundRunner(self)
        self._tokens: dict[int, int] = {}
        self.empty = Label("", "meta")
        self.empty.setWordWrap(True)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(16)
        self.body.addWidget(self.empty)
        self.body.addLayout(self.rows_box)
        self.retranslate()

    def set_experiment(self, session: Session | None, experiment_id: str | None) -> None:
        """切换试验：清空已显示的缩略图。

        Args:
            session: 当前会话。
            experiment_id: 试验标识，None 表示没有试验。
        """
        self._runner.cancel()
        self._tokens.clear()
        for widget in self._rows.values():
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._rows.clear()
        self._stale.clear()
        self._session = session
        self._experiment_id = experiment_id
        self.refresh()

    def mark_stale(self, row_index: int) -> None:
        """这一排有了新图（抓帧完成），下次刷新时重读。

        Args:
            row_index: 排号。
        """
        self._stale.add(row_index)

    def row_indexes(self) -> list[int]:
        """已列出的排号。

        Returns:
            升序排号。
        """
        return sorted(self._rows)

    def loaded_rows(self) -> list[int]:
        """已经显示出缩略图的排号。

        Returns:
            升序排号。
        """
        return sorted(r for r, w in self._rows.items() if w.loaded)

    def thumb(self, row_index: int) -> _RowThumb | None:
        """某排的缩略图控件（测试用）。

        Args:
            row_index: 排号。

        Returns:
            控件，没有时为 None。
        """
        return self._rows.get(row_index)

    def refresh(self) -> None:
        """列出有原图的排，并在后台读取还没有显示的缩略图。"""
        experiment_id = self._experiment_id
        if experiment_id is None:
            self.empty.setText(self.tr("没有选中的试验"))
            self.empty.setVisible(True)
            return
        try:
            rows = self._media.rows_with_images(self._session, experiment_id)
        except Exception:
            rows = []
        self.empty.setText(self.tr("这个试验还没有抓拍图像，每排读数后会显示在这里"))
        self.empty.setVisible(not rows)
        for row in rows:
            widget = self._rows.get(row)
            if widget is None:
                widget = _RowThumb(row)
                widget.retranslate()
                self._rows[row] = widget
                self.rows_box.insertWidget(sorted(self._rows).index(row), widget)
                self._load(row)
            elif row in self._stale:
                self._load(row)
        self._stale.clear()

    def _load(self, row: int) -> None:
        experiment_id = self._experiment_id
        if experiment_id is None:
            return
        session = self._session
        old = self._tokens.get(row)
        if old is not None:
            self._runner.cancel(old)

        def done(thumbs: RowThumbnails) -> None:
            widget = self._rows.get(row)
            if widget is not None:
                widget.show_thumbnails(thumbs)

        self._tokens[row] = self._runner.submit(
            lambda: self._media.row_thumbnails(session, experiment_id, row), done
        )

    def retranslate(self) -> None:
        """刷新文字。"""
        self.set_title(self.tr("图像显示"))
        for widget in self._rows.values():
            widget.retranslate()
        if self._experiment_id is not None and not self._rows:
            self.empty.setText(self.tr("这个试验还没有抓拍图像，每排读数后会显示在这里"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
