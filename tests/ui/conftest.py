"""界面测试夹具：离屏平台并加载主题与字体。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gelinspect.ui.theme.stylesheet import apply_theme  # noqa: E402


@pytest.fixture(autouse=True)
def themed(qapp: QApplication) -> None:
    apply_theme(qapp)
