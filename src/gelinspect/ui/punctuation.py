"""随语言变化的标点：中文用全角，英文用半角加空格。

界面里“正文（注释）”“标题：说明”这类拼接不能写死全角符号，否则英文界面会出现全角标点。
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QCoreApplication


def annotate(text: str, note: str) -> str:
    """给文字加括号注释。

    Args:
        text: 正文。
        note: 注释，例如快捷键。

    Returns:
        中文是“新试验（Ctrl+N）”，英文是 ``New experiment (Ctrl+N)``。
    """
    return QCoreApplication.translate("Punctuation", "{text}（{note}）").format(
        text=text, note=note
    )


def colon(text: str, note: str) -> str:
    """用冒号连接标题和说明。

    Args:
        text: 标题。
        note: 说明。

    Returns:
        中文是“标题：说明”，英文是 ``Title: note``。
    """
    return QCoreApplication.translate("Punctuation", "{text}：{note}").format(text=text, note=note)


def join_items(items: Iterable[object]) -> str:
    """用当前语言的列表分隔符连接多项（管号、排号等）。

    Args:
        items: 要连接的内容。

    Returns:
        中文用顿号，英文用逗号加空格。
    """
    separator = QCoreApplication.translate("Punctuation", "、")
    return separator.join(str(item) for item in items)
