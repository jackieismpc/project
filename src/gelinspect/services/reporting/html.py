"""把报告内容排成 Qt 富文本（HTML 子集），交给 ``QTextDocument`` 分页。

只用 ``QTextDocument`` 支持的 HTML 子集：表格、百分比宽度、``pt`` 字号、``<thead>`` 表头
（跨页重复）。长度单位里的 ``px`` 是 96 dpi 的逻辑像素，Qt 按输出设备的分辨率自动放大。
所有来自用户的文字（样品名、批号、人名）都转义。
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from html import escape

from PySide6.QtCore import QT_TRANSLATE_NOOP

from gelinspect.domain.conclusions import (
    NOTE_TEMPLATES,
    ConclusionNote,
    ExperimentConclusion,
    format_quantity,
)
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    Interpretation,
    ReportScope,
    TubeRole,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.reporting.document import (
    ExperimentSection,
    ReportDocument,
    ReportRowData,
)
from gelinspect.services.reporting.strings import S
from gelinspect.services.reporting.texts import ReportTexts

SANS = "Noto Sans SC"
"""正文字体（随软件内置）。"""
MONO = "IBM Plex Mono"
"""数字、批号用的等宽字体（随软件内置）。"""

INK = "#111111"
MUTED = "#555555"
HAIRLINE = "#d8d8d8"
WARN = "#9a3b00"

_BLANK = "–"

_TYPE_SOURCES = {
    ExperimentType.SENSITIVITY_CONFIRMATION: str(QT_TRANSLATE_NOOP("ExperimentType", "灵敏度复核")),
    ExperimentType.INTERFERENCE: str(QT_TRANSLATE_NOOP("ExperimentType", "干扰试验")),
    ExperimentType.SEMI_QUANTITATIVE: str(QT_TRANSLATE_NOOP("ExperimentType", "半定量试验")),
    ExperimentType.LIMIT: str(QT_TRANSLATE_NOOP("ExperimentType", "限度试验")),
}
_STATE_SOURCES = {
    ExperimentState.COMPLETED: str(QT_TRANSLATE_NOOP("ExperimentState", "已完成")),
    ExperimentState.SIGNED: str(QT_TRANSLATE_NOOP("ExperimentState", "已签署")),
}
_VERDICT_SOURCES = {
    ConclusionVerdict.PASS: str(QT_TRANSLATE_NOOP("Conclusion", "合格")),
    ConclusionVerdict.FAIL: str(QT_TRANSLATE_NOOP("Conclusion", "不合格")),
    ConclusionVerdict.INVALID: str(QT_TRANSLATE_NOOP("Conclusion", "无效")),
    ConclusionVerdict.PENDING: str(QT_TRANSLATE_NOOP("Conclusion", "待定")),
}
_ROLE_SOURCES = {
    TubeRole.TEST: str(QT_TRANSLATE_NOOP("TubeRole", "试验管")),
    TubeRole.NEGATIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "阴性对照")),
    TubeRole.SAMPLE: str(QT_TRANSLATE_NOOP("TubeRole", "供试品")),
    TubeRole.POSITIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "阳性对照")),
    TubeRole.SAMPLE_POSITIVE_CONTROL: str(QT_TRANSLATE_NOOP("TubeRole", "供试品阳性对照")),
    TubeRole.UNCONFIRMED: str(QT_TRANSLATE_NOOP("TubeRole", "用途待确认")),
}


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _mono(value: str) -> str:
    return f"<span style=\"font-family:'{MONO}'\">{_e(value)}</span>"


def note_text(texts: ReportTexts, note: ConclusionNote) -> str:
    """一条依据或前提的报告语言文字。

    Args:
        texts: 报告文字。
        note: 依据或前提。

    Returns:
        翻译并代入参数后的文字（未转义）。
    """
    params = dict(note.params)
    if "role" in params:
        params["role"] = texts.tr(_ROLE_SOURCES[TubeRole(params["role"])], "TubeRole")
    return texts.tr(NOTE_TEMPLATES[note.code], "Conclusion").format(**params)


def verdict_text(texts: ReportTexts, verdict: ConclusionVerdict) -> str:
    """结论的报告语言名称。

    Args:
        texts: 报告文字。
        verdict: 结论。

    Returns:
        例如“合格”。
    """
    return texts.tr(_VERDICT_SOURCES[verdict], "Conclusion")


def _stamp(value: datetime | None, zone: tzinfo | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return _BLANK
    return value.astimezone(zone).strftime(fmt)


def result_text(texts: ReportTexts, row: ReportRowData) -> str:
    """结果表“阴阳性”一列的文字，复核过的标注“已复核”。

    Args:
        texts: 报告文字。
        row: 结果表的一行。

    Returns:
        例如“阳性（已复核）”；没有结果时为“无结果”。
    """
    if row.interpretation is Interpretation.POSITIVE:
        text = texts.s(S.POSITIVE)
    elif row.interpretation is Interpretation.NEGATIVE:
        text = texts.s(S.NEGATIVE)
    elif row.interpretation is Interpretation.PENDING_REVIEW:
        return texts.s(S.PENDING)
    else:
        return texts.s(S.NO_RESULT)
    if row.reviewed:
        text += texts.s(S.REVIEWED)
    return text


class _Builder:
    def __init__(self, document: ReportDocument, texts: ReportTexts, zone: tzinfo | None) -> None:
        self.doc = document
        self.t = texts
        self.zone = zone

    def px(self, value: float) -> int:
        return max(1, round(value))

    def s(self, key: S, **params: object) -> str:
        return self.t.s(key, **params)

    def type_name(self, value: ExperimentType) -> str:
        return self.t.tr(_TYPE_SOURCES[value], "ExperimentType")

    # ------------------------------------------------------------------ 公共片段

    def title(self, text: str) -> str:
        return (
            f'<p align="center" style="margin-top:0;margin-bottom:{self.px(4)}px">'
            f'<span style="font-size:19pt;font-weight:700">{_e(text)}</span></p>'
        )

    def subtitle(self, text: str) -> str:
        return (
            f'<p align="center" style="margin-top:0;margin-bottom:{self.px(6)}px;color:{MUTED}">'
            f'<span style="font-size:9pt">{text}</span></p>'
        )

    def heading(self, text: str, *, page_break: bool = False) -> str:
        brk = "page-break-before:always;" if page_break else ""
        return (
            f'<p style="{brk}margin-top:0;margin-bottom:{self.px(6)}px">'
            f'<span style="font-size:12pt;font-weight:700">{text}</span></p>'
        )

    def gap(self, size: float = 8) -> str:
        return f'<p style="margin:0;font-size:{max(1, round(size * 0.75))}pt">&nbsp;</p>'

    # ------------------------------------------------------------------ 试验头部

    def _field_row(self, cells: list[tuple[str, str]]) -> str:
        """一行：每个字段是“标签 + 值”两格，最后一格没有下划线间隔。"""
        out = ["<tr>"]
        edge = f"border-bottom:{self.px(1)}px solid {HAIRLINE};"
        pad = f"padding-top:{self.px(4)}px;padding-bottom:{self.px(4)}px;"
        for label, value in cells:
            out.append(
                f'<td width="21%" style="{edge}{pad}color:{MUTED}">{_e(label)}</td>'
                f'<td width="29%" style="{edge}{pad}">{value}</td>'
            )
        out.append("</tr>")
        return "".join(out)

    def header(self, section: ExperimentSection) -> str:
        t, doc = self.t, self.doc
        lam = (
            _BLANK
            if section.labeled_sensitivity is None
            else _mono(t.number(section.labeled_sensitivity)) + " EU/mL"
        )
        temp = (
            _BLANK
            if section.temperature_range is None
            else _mono(f"{section.temperature_range[0]:.1f}–{section.temperature_range[1]:.1f}")
            + " ℃"
        )
        if section.signer_name is not None and section.signed_at is not None:
            signed = f"{_e(section.signer_name)} / {_mono(_stamp(section.signed_at, self.zone))}"
        elif doc.signed or section.state is ExperimentState.SIGNED:
            signed = _BLANK
        else:
            signed = f'<span style="color:{WARN}">{_e(self.s(S.UNSIGNED))}</span>'
        limit = "" if section.endotoxin_limit is None else _mono(t.number(section.endotoxin_limit))
        if limit:
            limit += " EU/mL"
        mvd = "" if section.mvd is None else _mono(t.number(section.mvd))
        rows = [
            [
                (self.s(S.FILE_NAME), _mono(section.experiment_id)),
                (self.s(S.INSTRUMENT_NO), _mono(section.instrument_no)),
            ],
            [
                (self.s(S.TEST_DATE), _mono(_stamp(section.started_at, self.zone))),
                (self.s(S.TEMPERATURE_RANGE), temp),
            ],
            [
                (self.s(S.OPERATOR), _e(section.operator_name)),
                (self.s(S.SIGNED_BY_DATE), signed),
            ],
            [
                (self.s(S.SENSITIVITY), lam),
                (self.s(S.LAL_BATCH), _mono(section.lal_batch or _BLANK)),
            ],
            [
                (self.s(S.ENDOTOXIN_BATCH), _mono(section.endotoxin_batch or _BLANK)),
                (self.s(S.WATER_BATCH), _mono(section.water_batch or _BLANK)),
            ],
            [(self.s(S.MVD), mvd), (self.s(S.ENDOTOXIN_LIMIT), limit)],
        ]
        body = "".join(self._field_row(r) for r in rows)
        return (
            f'<table width="100%" cellspacing="0" cellpadding="{self.px(1)}" '
            f'style="font-size:9pt;border-collapse:collapse">{body}</table>'
        )

    # ------------------------------------------------------------------ 结果表

    def results(self, section: ExperimentSection) -> str:
        t = self.t
        top = f"border-top:{self.px(2)}px solid {INK};border-bottom:{self.px(1)}px solid {INK};"
        cell = f"border-bottom:{self.px(1)}px solid {HAIRLINE};"
        pad = f"padding-top:{self.px(2)}px;padding-bottom:{self.px(2)}px;"
        heads = [
            (self.s(S.COL_TUBE), "10%", "left"),
            (self.s(S.COL_SAMPLE), "18%", "left"),
            (self.s(S.COL_SERIAL), "12%", "left"),
            (self.s(S.SAMPLE_BATCH), "13%", "left"),
            (self.s(S.COL_DILUTION), "11%", "right"),
            (self.s(S.COL_SPIKED), "18%", "right"),
            (self.s(S.COL_RESULT), "18%", "left"),
        ]
        head = "".join(
            f'<th width="{w}" align="{a}" style="{top}{pad}font-weight:500">{_e(h)}</th>'
            for h, w, a in heads
        )
        body: list[str] = []
        for row in section.rows:
            positive = row.interpretation is Interpretation.POSITIVE
            weight = "font-weight:700;" if positive else ""
            pending = row.interpretation in (None, Interpretation.PENDING_REVIEW)
            colour = f"color:{WARN};" if pending else ""
            body.append(
                "<tr>"
                f'<td style="{cell}{pad}">{_mono(str(row.tube_no))}</td>'
                f'<td style="{cell}{pad}">{_e(t.builtin_name(row.sample_name))}</td>'
                f'<td style="{cell}{pad}">{_e(row.serial_no or _BLANK)}</td>'
                f'<td style="{cell}{pad}">{_e(row.batch_no or _BLANK)}</td>'
                f'<td align="right" style="{cell}{pad}">'
                f"{_mono(t.number(row.dilution_factor))}</td>"
                f'<td align="right" style="{cell}{pad}">'
                f"{_mono(t.number(row.spiked_endotoxin))}</td>"
                f'<td style="{cell}{pad}{weight}{colour}">{_e(result_text(t, row))}</td>'
                "</tr>"
            )
        return (
            f'<table width="100%" cellspacing="0" cellpadding="{self.px(2)}" '
            'style="font-size:8.5pt;border-collapse:collapse">'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        )

    # ------------------------------------------------------------------ 结论区

    def _notes(self, notes: tuple[ConclusionNote, ...]) -> list[str]:
        return [
            f'<span style="font-size:8.5pt">{_e(note_text(self.t, note))}</span>' for note in notes
        ]

    def conclusion(self, section: ExperimentSection) -> str:
        t = self.t
        conclusion: ExperimentConclusion | None = section.conclusion
        parts: list[str] = []
        label = _e(self.s(S.CONCLUSION))
        if not section.conclusions_enabled:
            # 结论功能关闭：不出现结论、λc、依据、规则版本，只保留复核说明。
            pass
        elif conclusion is None:
            parts.append(
                f'<span style="font-size:10.5pt;font-weight:700">{label}：'
                f"{_e(self.s(S.NONE))}</span>"
            )
            parts.append(f'<span style="font-size:8.5pt">{_e(self.s(S.NO_CONCLUSION))}</span>')
        else:
            verdict = verdict_text(t, conclusion.verdict)
            final = conclusion.verdict in (ConclusionVerdict.PASS, ConclusionVerdict.FAIL)
            headline = f"{label}：{_e(verdict)}"
            if final and conclusion.value is not None:
                headline += (
                    f"　λc = {_mono(format_quantity(conclusion.value))} EU/mL"
                    f"（{_e(self.s(S.MEASURED))}）"
                )
            if conclusion.verdict is ConclusionVerdict.PENDING:
                headline += _e(self.s(S.NOT_FINAL_HINT))
            parts.append(f'<span style="font-size:10.5pt;font-weight:700">{headline}</span>')
            if not final and conclusion.value is not None:
                parts.append(
                    f'<span style="font-size:8.5pt">'
                    f"{_e(self.s(S.LAMBDA_C_REFERENCE))} {_mono(format_quantity(conclusion.value))}"
                    f" EU/mL（{_e(self.s(S.NOT_FINAL))}）</span>"
                )
            if conclusion.unmet:
                title = {
                    ConclusionVerdict.INVALID: S.REASON_INVALID,
                    ConclusionVerdict.PENDING: S.REASON_PENDING,
                }.get(conclusion.verdict, S.REASON_UNMET)
                parts.append(
                    f'<span style="font-size:8.5pt;font-weight:700;color:{WARN}">'
                    f"{_e(self.s(title))}</span>"
                )
                parts.extend(self._notes(conclusion.unmet))
            if conclusion.basis:
                parts.append(
                    f'<span style="font-size:8.5pt;font-weight:700">{_e(self.s(S.BASIS))}</span>'
                )
                parts.extend(self._notes(conclusion.basis))
            parts.append(
                f'<span style="font-size:7.5pt;color:{MUTED}">'
                f"{_e(self.s(S.RULE_VERSION))} {_mono(conclusion.rule_version)} · "
                f"{_e(self.s(S.RULE_SOURCE))}："
                f"{_e(self.t.tr(conclusion.rule_source, 'Conclusion'))}</span>"
            )
        if any(row.reviewed for row in section.rows):
            parts.append(
                f'<span style="font-size:7.5pt;color:{MUTED}">{_e(self.s(S.REVIEWED_NOTE))}</span>'
            )
        return self._box(parts) if parts else ""

    def _box(self, lines: list[str]) -> str:
        """把若干行放进带边框的表格：每行一个表格行，分页时在行之间断开。"""
        side = f"border-left:{self.px(1)}px solid {INK};border-right:{self.px(1)}px solid {INK};"
        rows = []
        for index, line in enumerate(lines):
            edge = side
            if index == 0:
                edge += f"border-top:{self.px(1)}px solid {INK};"
            if index == len(lines) - 1:
                edge += f"border-bottom:{self.px(1)}px solid {INK};"
            pad_top = self.px(5) if index == 0 else self.px(1)
            pad_bottom = self.px(5) if index == len(lines) - 1 else self.px(1)
            rows.append(
                f'<tr><td style="{edge}padding-left:{self.px(6)}px;padding-right:{self.px(6)}px;'
                f'padding-top:{pad_top}px;padding-bottom:{pad_bottom}px">{line}</td></tr>'
            )
        return (
            '<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="border-collapse:collapse">{"".join(rows)}</table>'
        )

    # ------------------------------------------------------------------ 8 连图

    def images(self, section: ExperimentSection, width_px: int) -> str:
        pairs = self.doc.images.get(section.experiment_id, ())
        if not pairs:
            return ""
        each = max(1, (width_px - self.px(24)) // 2)
        out = [
            self.heading(_e(self.s(S.IMAGES_TITLE)), page_break=True),
            f'<p style="margin:0 0 {self.px(6)}px 0;color:{MUTED}"><span style="font-size:8pt">'
            f"{_e(section.experiment_id)}</span></p>",
        ]
        for pair in pairs:
            cells = []
            for phase, caption in (
                ("before", self.s(S.BEFORE)),
                ("after", self.s(S.AFTER)),
            ):
                image = pair.before if phase == "before" else pair.after
                if image is None:
                    body = f'<span style="color:{WARN}">{_e(self.s(S.NO_IMAGE))}</span>'
                else:
                    name = image_resource_name(section.experiment_id, pair.row_index, phase)
                    body = f'<img src="{_e(name)}" width="{each}">'
                label = f'<span style="font-size:7pt;color:{MUTED}">{_e(caption)}</span><br>'
                cells.append(f'<td width="50%" valign="top">{label}{body}</td>')
            out.append(
                f'<p style="margin:{self.px(4)}px 0 {self.px(2)}px 0"><span style="font-size:8pt">'
                f"{_e(self.s(S.ROW_N).format(row=pair.row_index))}</span></p>"
                f'<table width="100%" cellspacing="0" cellpadding="{self.px(2)}">'
                f"<tr>{''.join(cells)}</tr></table>"
            )
        return "".join(out)

    # ------------------------------------------------------------------ 整份报告

    def section_body(self, section: ExperimentSection, width_px: int) -> str:
        conclusion = self.conclusion(section)
        return (
            self.header(section)
            + self.gap(10)
            + self.results(section)
            + (self.gap(10) + conclusion if conclusion else "")
            + self.images(section, width_px)
        )

    def summary(self) -> str:
        """单批次报告的汇总表：每个试验一行。"""
        t = self.t
        top = f"border-top:{self.px(2)}px solid {INK};border-bottom:{self.px(1)}px solid {INK};"
        cell = f"border-bottom:{self.px(1)}px solid {HAIRLINE};"
        with_verdict = any(section.conclusions_enabled for section in self.doc.sections)
        heads = [
            (self.s(S.COL_EXPERIMENT), "26%" if with_verdict else "30%"),
            (self.s(S.EXPERIMENT_TYPE), "16%" if with_verdict else "20%"),
            (self.s(S.TEST_DATE), "20%" if with_verdict else "24%"),
            (self.s(S.COL_COUNT), "10%" if with_verdict else "12%"),
        ]
        if with_verdict:
            heads.append((self.s(S.CONCLUSION), "14%"))
        heads.append((self.s(S.COL_STATE), "14%"))
        head = "".join(
            f'<th width="{w}" align="left" style="{top}font-weight:500">{_e(h)}</th>'
            for h, w in heads
        )
        body = []
        for section in self.doc.sections:
            conclusion = section.conclusion
            verdict = self.s(S.NONE) if conclusion is None else verdict_text(t, conclusion.verdict)
            state_source = _STATE_SOURCES.get(section.state)
            state = (
                self.s(S.UNSIGNED)
                if state_source is None
                else t.tr(state_source, "ExperimentState")
            )
            verdict_cell = f'<td style="{cell}">{_e(verdict)}</td>' if with_verdict else ""
            body.append(
                "<tr>"
                f'<td style="{cell}">{_mono(section.experiment_id)}</td>'
                f'<td style="{cell}">{_e(self.type_name(section.experiment_type))}</td>'
                f'<td style="{cell}">{_mono(_stamp(section.started_at, self.zone))}</td>'
                f'<td style="{cell}">{_mono(str(len(section.rows)))}</td>'
                f"{verdict_cell}"
                f'<td style="{cell}">{_e(state)}</td>'
                "</tr>"
            )
        return (
            f'<table width="100%" cellspacing="0" cellpadding="{self.px(2)}" '
            'style="font-size:8.5pt;border-collapse:collapse">'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        )

    def build(self, width_px: int) -> str:
        doc = self.doc
        parts: list[str] = []
        if doc.scope is ReportScope.EXPERIMENT:
            parts.append(self.title(self.s(S.TITLE)))
            parts.append(self.subtitle(_e(self.type_name(doc.sections[0].experiment_type))))
            parts.append(self.section_body(doc.sections[0], width_px))
        else:
            parts.append(self.title(self.s(S.BATCH_TITLE)))
            kind = (
                self.s(S.LAL_BATCH)
                if doc.batch_kind is not None and doc.batch_kind.value == "lal_reagent"
                else self.s(S.SAMPLE_BATCH)
            )
            signed = (
                _e(self.s(S.SIGNED_BY_DATE))
                + "："
                + (
                    f"{_e(doc.signed_by)} / {_mono(_stamp(doc.signed_at, self.zone))}"
                    if doc.signed_by
                    else _e(self.s(S.UNSIGNED))
                )
            )
            parts.append(
                self.subtitle(
                    f"{_e(kind)}：{_mono(doc.batch_no or _BLANK)} · "
                    f"{_e(self.s(S.EXPERIMENT_COUNT).format(n=len(doc.sections)))} · {signed}"
                )
            )
            parts.append(self.summary())
            for section in doc.sections:
                parts.append(
                    self.heading(
                        f"{_e(self.s(S.EXPERIMENT))} {_mono(section.experiment_id)}",
                        page_break=True,
                    )
                )
                parts.append(self.section_body(section, width_px))
        body = "".join(parts)
        return (
            "<html><head><meta charset='utf-8'></head>"
            f"<body style=\"font-family:'{SANS}';font-size:9pt;color:{INK}\">{body}</body></html>"
        )


def image_resource_name(experiment_id: str, row_index: int, phase: str) -> str:
    """8 连图在 ``QTextDocument`` 里的资源名。

    Args:
        experiment_id: 试验标识。
        row_index: 排号。
        phase: ``before`` 或 ``after``。

    Returns:
        资源名（用作 ``<img src>`` 和 ``addResource`` 的 URL）。
    """
    return f"gelinspect-img://{experiment_id}/{row_index}/{phase}"


def render_html(
    document: ReportDocument,
    texts: ReportTexts,
    *,
    width_px: int = 1000,
    zone: tzinfo | None = None,
) -> str:
    """生成整份报告的 HTML。

    Args:
        document: 报告内容。
        texts: 报告语言的文字。
        width_px: 版心宽度（逻辑像素，96 dpi），用来确定图像尺寸。
        zone: 显示时间用的时区，默认本机时区。

    Returns:
        HTML 文本。
    """
    return _Builder(document, texts, zone).build(width_px)
