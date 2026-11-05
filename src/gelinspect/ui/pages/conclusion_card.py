"""试验结论卡：结论徽标、λc、依据、未满足的前提和规则版本。

内容全部来自入库的结论（或试验未完成时的临时结论），这里只负责显示，不做任何判定。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.domain.conclusions import ConclusionNote, ExperimentConclusion
from gelinspect.domain.enums import ConclusionVerdict
from gelinspect.ui.labels import (
    conclusion_note_text,
    conclusion_source_label,
    conclusion_verdict_label,
    format_concentration,
)
from gelinspect.ui.widgets.badge import BadgeTone, StatusBadge
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.label import Label

_TONES = {
    ConclusionVerdict.PASS: BadgeTone.SUCCESS,
    ConclusionVerdict.FAIL: BadgeTone.DANGER,
    ConclusionVerdict.INVALID: BadgeTone.DANGER,
    ConclusionVerdict.PENDING: BadgeTone.WARNING,
}


class ConclusionCard(Card):
    """试验结论卡。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建卡片。

        Args:
            parent: 父控件。
        """
        super().__init__(" ", parent=parent)
        self._conclusion: ExperimentConclusion | None = None
        self._recorded = False
        assert self.header is not None
        self.rule_label = Label("", "meta")
        self.header.addWidget(self.rule_label)
        self.badge = StatusBadge("", BadgeTone.WARNING)
        self.header.addWidget(self.badge)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        self.value_cell = QWidget()
        value_layout = QVBoxLayout(self.value_cell)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(2)
        self.value_title = Label("", "lab")
        self.value_label = Label("", "readout", mono=True)
        self.lambda_label = Label("", "meta", mono=True)
        value_layout.addWidget(self.value_title)
        value_layout.addWidget(self.value_label)
        value_layout.addWidget(self.lambda_label)
        value_layout.addStretch(1)
        columns.addWidget(self.value_cell, 0)

        text = QVBoxLayout()
        text.setSpacing(4)
        self.unmet_title = Label("", "lab")
        self.unmet_label = Label("", "danger")
        self.unmet_label.setWordWrap(True)
        self.basis_title = Label("", "lab")
        self.basis_label = Label("", "body")
        self.basis_label.setWordWrap(True)
        self.basis_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_label = Label("", "meta")
        self.source_label.setWordWrap(True)
        for widget in (
            self.unmet_title,
            self.unmet_label,
            self.basis_title,
            self.basis_label,
            self.source_label,
        ):
            text.addWidget(widget)
        columns.addLayout(text, 1)
        self.body.addLayout(columns)
        self.retranslate()

    def set_conclusion(self, conclusion: ExperimentConclusion, *, recorded: bool) -> None:
        """显示结论。

        Args:
            conclusion: 结论。
            recorded: 是否已入库。没入库的是试验未完成时的临时结论。
        """
        self._conclusion = conclusion
        self._recorded = recorded
        self._render()

    def _notes(self, notes: tuple[ConclusionNote, ...]) -> str:
        return "\n".join(conclusion_note_text(note) for note in notes)

    def _render(self) -> None:
        conclusion = self._conclusion
        if conclusion is None:
            return
        self.badge.set_text(conclusion_verdict_label(conclusion.verdict))
        self.badge.set_tone(_TONES[conclusion.verdict])
        self.rule_label.setText(
            self.tr("规则版本 {version}").format(version=conclusion.rule_version)
        )
        final = conclusion.verdict in (ConclusionVerdict.PASS, ConclusionVerdict.FAIL)
        has_value = conclusion.value is not None
        self.value_cell.setVisible(has_value)
        if has_value and conclusion.value is not None:
            self.value_title.setText(
                self.tr("实测灵敏度 λc") if final else self.tr("λc（参考值，不是最终结论）")
            )
            self.value_label.setText(f"{format_concentration(conclusion.value)} EU/ml")
            labeled = conclusion.labeled_sensitivity
            self.lambda_label.setText(
                ""
                if labeled is None
                else self.tr("标示灵敏度 λ {value} EU/ml").format(
                    value=format_concentration(labeled)
                )
            )
        unmet = self._notes(conclusion.unmet)
        self.unmet_title.setText(
            self.tr("无效原因")
            if conclusion.verdict is ConclusionVerdict.INVALID
            else self.tr("未满足的前提")
        )
        self.unmet_title.setVisible(bool(unmet))
        self.unmet_label.setText(unmet)
        invalid = conclusion.verdict in (ConclusionVerdict.INVALID, ConclusionVerdict.FAIL)
        self.unmet_label.set_role("danger" if invalid else "warn")
        self.unmet_label.setVisible(bool(unmet))
        basis = self._notes(conclusion.basis)
        self.basis_title.setVisible(bool(basis))
        self.basis_label.setText(basis)
        self.basis_label.setVisible(bool(basis))
        source = self.tr("规则来源：{source}").format(
            source=conclusion_source_label(conclusion.rule_source)
        )
        if not self._recorded:
            source = self.tr("试验完成后才会固化结论。") + source
        self.source_label.setText(source)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.set_title(self.tr("试验结论"))
        self.basis_title.setText(self.tr("依据（+ 阳性，− 阴性）"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
