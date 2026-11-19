"""用 ``QTextDocument`` 排版、``QPdfWriter`` 输出 A4 PDF；同一套排版也用于预览和打印。

* 只用 PySide6-Essentials 里的 ``QtGui``，不引入 QtPdf（Addons）或其他 PDF 库。
* 英文与数字用 Times New Roman，中文用 SimSun。字体由目标操作系统提供，不随软件分发；
  系统缺少时会记录警告并由系统字体回退。
* 页脚（不可更改声明、版权声明、内容摘要、页码）逐页自绘，因为 ``QTextDocument`` 没有页眉页脚。
* 排版只依赖“设备”（``QPagedPaintDevice``）：``QPdfWriter`` 出文件，``QPrinter`` 供预览、打印。
* 服务层可以在没有窗口的环境里运行：没有 ``QGuiApplication`` 时用 ``offscreen`` 平台创建一个。
  ``QTextDocument`` 和字体库只能在创建它的线程里用，所以调用方要在主线程渲染。
"""

from __future__ import annotations

import logging
import os
from datetime import tzinfo

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QMarginsF,
    QRectF,
    QSizeF,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QPagedPaintDevice,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextDocument,
    QTextOption,
)

from gelinspect.services.reporting.document import ReportDocument
from gelinspect.services.reporting.html import (
    CJK,
    MONO,
    SANS,
    TITLE,
    image_resource_name,
    render_html,
)
from gelinspect.services.reporting.strings import DEFAULT_HOLDER, SOURCES, S
from gelinspect.services.reporting.texts import ReportTexts

_log = logging.getLogger(__name__)

PDF_RESOLUTION = 288
"""PDF 排版分辨率（dpi），是 96 的整数倍，逻辑像素到设备像素的换算因此是整数。"""
MARGINS_MM = QMarginsF(14, 14, 14, 10)
"""页边距（左、上、右、下），毫米。"""
FOOTER_MM = 15.0
"""页脚高度（毫米），在版心内的最下方。"""

_app: QGuiApplication | None = None
_fonts_loaded = False
_report_fonts_available = False


def ensure_gui_application() -> QGuiApplication:
    """取得 ``QGuiApplication``；没有时用 ``offscreen`` 平台创建一个（无窗口环境）。

    已经有应用（界面进程里是 ``QApplication``）时直接返回它。

    Returns:
        应用对象。
    """
    global _app
    existing = QGuiApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QGuiApplication([])
    return _app


def configure_printer(printer: QPagedPaintDevice) -> None:
    """让打印机或预览设备用和 PDF 一样的纸张和页边距（A4）。

    Args:
        printer: ``QPrinter``（预览、打印用的分页设备）。
    """
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageMargins(MARGINS_MM, QPageLayout.Unit.Millimeter)


def ensure_fonts() -> bool:
    """Check whether the requested system report fonts are installed.

    Returns:
        True when Times New Roman, SimSun and SimHei are available; missing fonts fall back.
    """
    global _fonts_loaded, _report_fonts_available
    ensure_gui_application()
    if not _fonts_loaded:
        families = set(QFontDatabase.families())
        _report_fonts_available = all(name in families for name in (SANS, CJK, TITLE))
        if SANS not in families:
            _log.warning("报告字体 %s 不可用，报告回退到系统字体", SANS)
        if CJK not in families:
            _log.warning("报告字体 %s 不可用，报告回退到系统字体", CJK)
        if TITLE not in families:
            _log.warning("报告字体 %s 不可用，报告回退到系统字体", TITLE)
        _fonts_loaded = True
    return _report_fonts_available


class ReportRenderer:
    """把 :class:`ReportDocument` 排版成 PDF，或画到打印机、预览设备上。"""

    def __init__(self, *, zone: tzinfo | None = None) -> None:
        """创建渲染器。

        Args:
            zone: 显示时间用的时区，默认本机时区。
        """
        self._zone = zone
        self.last_ideal_width = 0.0
        """最近一次排版里内容的理想宽度（设备像素），测试用来检查没有超出版心。"""
        self.last_page_width = 0.0
        """最近一次排版的版心宽度（设备像素）。"""

    # ------------------------------------------------------------------ 输出 PDF

    def render_pdf(self, document: ReportDocument, texts: ReportTexts) -> tuple[bytes, int]:
        """生成 A4 PDF（未签名）。

        Args:
            document: 报告内容。
            texts: 报告语言的文字。

        Returns:
            ``(PDF 字节, 页数)``。

        Raises:
            RuntimeError: 排版或输出失败。
        """
        ensure_fonts()
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError("无法创建内存缓冲区")
        writer = QPdfWriter(buffer)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(MARGINS_MM, QPageLayout.Unit.Millimeter)
        writer.setResolution(PDF_RESOLUTION)
        writer.setPdfVersion(QPagedPaintDevice.PdfVersion.PdfVersion_1_6)
        first = document.sections[0].experiment_id if document.sections else ""
        writer.setTitle(texts.s(S.META_TITLE, id=first))
        writer.setCreator("GelInspect")
        pages = self.paint(document, texts, writer)
        buffer.close()
        data = bytes(buffer.data().data())
        if not data.startswith(b"%PDF"):
            raise RuntimeError("PDF 输出为空")
        return data, pages

    # ------------------------------------------------------------------ 排版与绘制

    def paint(self, document: ReportDocument, texts: ReportTexts, device: QPagedPaintDevice) -> int:
        """把报告画到分页设备上（PDF 写入器、打印机或预览）。

        Args:
            document: 报告内容。
            texts: 报告语言的文字。
            device: 分页设备，页面尺寸和页边距取自设备本身。

        Returns:
            总页数。

        Raises:
            RuntimeError: 无法在设备上开始绘制。
        """
        ensure_fonts()
        resolution = device.logicalDpiX()
        paint_rect = device.pageLayout().paintRectPixels(resolution)
        width, height = paint_rect.width(), paint_rect.height()
        footer = round(FOOTER_MM / 25.4 * resolution)
        page_height = height - footer
        logical_width = round(width * 96.0 / resolution)

        text_document = QTextDocument()
        text_document.documentLayout().setPaintDevice(device)
        text_document.setDocumentMargin(0)
        font = QFont(SANS)
        font.setFamilies([SANS, CJK])
        font.setPointSizeF(9)
        text_document.setDefaultFont(font)
        for experiment_id, pairs in document.images.items():
            for pair in pairs:
                for phase, image in (("before", pair.before), ("after", pair.after)):
                    if image is not None:
                        text_document.addResource(
                            QTextDocument.ResourceType.ImageResource,
                            QUrl(image_resource_name(experiment_id, pair.row_index, phase)),
                            image,
                        )
        # 没有空格的长串（批号、样品名）也要能换行，否则会撑出版心。
        option = text_document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_document.setDefaultTextOption(option)
        text_document.setPageSize(QSizeF(width, page_height))
        text_document.setHtml(render_html(document, texts, width_px=logical_width, zone=self._zone))
        text_document.setPageSize(QSizeF(width, page_height))
        pages = max(1, text_document.pageCount())
        self.last_ideal_width = text_document.idealWidth()
        self.last_page_width = float(width)

        painter = QPainter()
        if not painter.begin(device):
            raise RuntimeError("无法在输出设备上开始绘制")
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            digest = document.content_digest()
            for page in range(pages):
                if page:
                    device.newPage()
                painter.save()
                painter.translate(0, -page * page_height)
                text_document.drawContents(
                    painter, QRectF(0, page * page_height, width, page_height)
                )
                painter.restore()
                self._footer(
                    painter, document, texts, digest, page + 1, pages, width, page_height, footer
                )
        finally:
            painter.end()
        return pages

    def _footer(
        self,
        painter: QPainter,
        document: ReportDocument,
        texts: ReportTexts,
        digest: str,
        page: int,
        pages: int,
        width: int,
        top: int,
        height: int,
    ) -> None:
        """画页脚：声明、版权、内容摘要和页码。"""
        painter.save()
        pad = round(height * 0.12)
        painter.setPen(Qt.GlobalColor.black)
        painter.drawLine(0, top + pad, width, top + pad)
        notes = QFont(SANS)
        notes.setFamilies([SANS, CJK])
        notes.setPointSizeF(7)
        painter.setFont(notes)
        painter.setPen(Qt.GlobalColor.darkGray)
        statement = texts.s(S.FOOT_SIGNED if document.signed else S.FOOT_UNSIGNED)
        holder = document.copyright_holder
        if holder == DEFAULT_HOLDER:
            holder = texts.s(S.HOLDER_PLACEHOLDER)
        elif holder == SOURCES[S.CLIENT_COMPANY]:
            holder = texts.s(S.CLIENT_COMPANY)  # 默认的甲方公司名称按报告语言显示
        copyright_text = texts.s(S.FOOT_COPYRIGHT, holder=holder)
        line_height = round((height - pad) / 3)
        wrap = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        painter.drawText(
            QRectF(0, top + pad * 1.6, width, line_height * 2),
            int(wrap),
            f"{statement} {copyright_text}",
        )
        # 摘要标签含汉字，按系统 SimSun 字体回退绘制；摘要数字使用 Times New Roman。
        mono = QFont(MONO)
        mono.setFamilies([MONO, CJK])
        mono.setPointSizeF(6.5)
        base = top + pad * 1.6 + line_height * 2
        label = f"{texts.s(S.FOOT_DIGEST)} "
        painter.setFont(notes)
        label_width = QFontMetricsF(notes, painter.device()).horizontalAdvance(label)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(QRectF(0, base, width * 0.78, line_height), flags, label)
        painter.setFont(mono)
        painter.drawText(
            QRectF(label_width, base, max(0.0, width * 0.78 - label_width), line_height),
            flags,
            digest,
        )
        painter.setFont(notes)
        painter.drawText(
            QRectF(width * 0.78, base, width * 0.22, line_height),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            texts.s(S.FOOT_PAGE, page=page, total=pages),
        )
        painter.restore()
