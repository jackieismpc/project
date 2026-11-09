"""判读依据对话框：凝胶法的判读标准、示例照片、软件的判读步骤和当前的阴阳性映射。

从单管详情页“判读过程”卡片打开，帮助分析员理解算法结果是怎么得出的。示例照片（
``ui/assets/reference``）只作说明，不参与判读。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import GelState
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.ui.pages.monitor_format import gel_state_label, interpretation_label
from gelinspect.ui.theme.fonts import ASSETS_DIR
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label

REFERENCE_DIR = ASSETS_DIR / "reference"
"""示例照片目录。"""
POSITIVE_PHOTO = REFERENCE_DIR / "gel_positive.png"
"""示例：形成凝胶、倒置不脱落（阳性）。"""
NEGATIVE_PHOTO = REFERENCE_DIR / "gel_negative.png"
"""示例：未形成凝胶（阴性）。"""
_PHOTO_WIDTH = 260


class JudgmentGuideDialog(QDialog):
    """判读依据对话框。"""

    def __init__(self, mapping: GelStateMapping, parent: QWidget | None = None) -> None:
        """创建对话框。

        Args:
            mapping: 当前配置的三态到阴阳性映射。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        title = self.tr("判读依据")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setModal(True)
        self.setMinimumWidth(620)
        self.resize(660, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        page = QWidget()
        page.setProperty("role", "dialog")
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 8)
        layout.setSpacing(10)
        layout.addWidget(Label(title, "h2"))

        layout.addWidget(Label(self.tr("凝胶法判读标准"), "lab"))
        layout.addWidget(
            self._body(
                self.tr(
                    "按中国药典通则 1143 凝胶法：保温 60±2 分钟后，将试管轻轻取出，缓缓倒转 180°。"
                    "管内形成凝胶，并且凝胶不变形、不从管壁滑脱的，为阳性；未形成凝胶，"
                    "或形成的凝胶不能保持完整并从管壁滑脱的，为阴性。"
                )
            )
        )

        photos = QHBoxLayout()
        photos.setSpacing(16)
        photos.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.positive_photo = self._photo(
            POSITIVE_PHOTO, self.tr("示例：形成凝胶，倒置后不脱落（阳性）")
        )
        self.negative_photo = self._photo(NEGATIVE_PHOTO, self.tr("示例：未形成凝胶（阴性）"))
        photos.addLayout(self.positive_photo[1])
        photos.addLayout(self.negative_photo[1])
        photos.addStretch(1)
        layout.addLayout(photos)
        layout.addWidget(self._meta(self.tr("示例照片只用于说明，不参与判读。")))

        layout.addSpacing(6)
        layout.addWidget(Label(self.tr("软件的判读步骤"), "lab"))
        steps = (
            self.tr("取图：每排翻转前、翻转后各取至少 3 帧图像。"),
            self.tr("分割：在图像中识别每支试管的试管区域和凝胶区域。"),
            self.tr(
                "特征：计算面积变化率 ΔA、试管坐标系下的质心偏移 Δs，"
                "以及多帧之间的面积波动 σ_A 和位置波动 σ_s。"
            ),
            self.tr("评分：每项特征归一化到 0–1（越大越像凝胶），按权重加权得到融合评分 Score。"),
            self.tr(
                "三态：Score 不低于完全凝固阈值为完全凝固，不高于未凝固阈值为未凝固，"
                "其余为部分凝固。"
            ),
            self.tr(
                "阴阳性：按下表换算；图像质量差、找不到试管、分析失败的试管一律待人工复核，"
                "不会默认判为阴性。"
            ),
        )
        for index, text in enumerate(steps, start=1):
            layout.addWidget(self._body(f"{index}. {text}"))

        layout.addSpacing(6)
        layout.addWidget(Label(self.tr("凝固状态与阴阳性（当前配置）"), "lab"))
        table = QGridLayout()
        table.setHorizontalSpacing(24)
        table.setVerticalSpacing(4)
        self.mapping_rows: list[tuple[str, str]] = []
        for row, state in enumerate(
            (GelState.FULLY_GELLED, GelState.PARTIALLY_GELLED, GelState.NOT_GELLED)
        ):
            state_text = gel_state_label(state)
            result_text = interpretation_label(mapping.for_state(state))
            self.mapping_rows.append((state_text, result_text))
            table.addWidget(Label(state_text, "body"), row, 0)
            table.addWidget(Label("→ " + result_text, "body"), row, 1)
        table.setColumnStretch(2, 1)
        layout.addLayout(table)
        if mapping.low_confidence_threshold is not None:
            layout.addWidget(
                self._meta(
                    self.tr("置信度低于 {t} 的结果也转人工复核。").format(
                        t=f"{mapping.low_confidence_threshold:g}"
                    )
                )
            )
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 0, 24, 0)
        buttons.addStretch(1)
        self.close_button = Button(self.tr("关闭"), ButtonVariant.PRIMARY)
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)

    @staticmethod
    def _body(text: str) -> Label:
        label = Label(text, "body")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _meta(text: str) -> Label:
        label = Label(text, "meta")
        label.setWordWrap(True)
        return label

    def _photo(self, path: object, caption: str) -> tuple[QLabel, QVBoxLayout]:
        """示例照片加说明；文件缺失时只显示说明。"""
        box = QVBoxLayout()
        box.setSpacing(4)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        image = QLabel()
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            image.setPixmap(
                pixmap.scaledToWidth(_PHOTO_WIDTH, Qt.TransformationMode.SmoothTransformation)
            )
        image.setAccessibleName(caption)
        box.addWidget(image)
        text = Label(caption, "meta")
        text.setWordWrap(True)
        text.setMaximumWidth(_PHOTO_WIDTH)
        box.addWidget(text)
        return image, box
