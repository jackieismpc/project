"""报告文字：简体中文和英文，与界面语言无关。

报告的语言由用户选择（默认跟随界面语言），而不是由「当前安装的翻译器」决定，所以这里直接读
``translations/gelinspect_<语言>.ts``，按（上下文，原文）查表，不经过 ``QCoreApplication``。
新文字写在本模块的 :data:`CONTEXT` 上下文里，用 ``QT_TRANSLATE_NOOP`` 标记，由
``scripts/update_translations.py`` 收集；试验结论、试管用途的名称复用界面已有的译文。

查不到译文时回退到简体中文原文，并记一条警告日志，不会让报告生成失败。
"""

from __future__ import annotations

import logging
import threading
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from gelinspect.services.reporting.strings import SOURCES, S
from gelinspect.services.runtime_paths import translations_dir

_log = logging.getLogger(__name__)

CONTEXT = "Report"
"""报告专用文字的翻译上下文。"""


class ReportLanguage(StrEnum):
    """报告语言。值是翻译文件名里的语言代码。"""

    ZH_CN = "zh_CN"
    """简体中文，源语言。"""
    EN = "en"
    """英文。"""


Catalog = Mapping[tuple[str, str], str]


def _read_catalog(path: Path) -> dict[tuple[str, str], str]:
    """读取 ``.ts``，返回（上下文，原文）到译文的映射，未完成或为空的译文不收录。"""
    catalog: dict[tuple[str, str], str] = {}
    for context in ET.parse(path).getroot().iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            translation = message.find("translation")
            if translation is None or translation.get("type") == "unfinished":
                continue
            text = "".join(translation.itertext())
            source = message.findtext("source") or ""
            if text and source:
                catalog[(name, source)] = text
    return catalog


_cache: dict[tuple[Path, float], dict[tuple[str, str], str]] = {}
_cache_lock = threading.Lock()


def load_catalog(language: ReportLanguage, directory: Path | None = None) -> Catalog:
    """加载某语言的译文表（按文件修改时间缓存）。

    Args:
        language: 报告语言；简体中文是源语言，返回空表。
        directory: 翻译目录，默认仓库根目录下的 ``translations/``。

    Returns:
        译文表。翻译文件不存在时为空表（报告回退为简体中文并记警告）。
    """
    if language is ReportLanguage.ZH_CN:
        return {}
    path = (directory or translations_dir()) / f"gelinspect_{language.value}.ts"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        _log.warning("缺少翻译文件 %s，报告回退为简体中文", path)
        return {}
    with _cache_lock:
        key = (path, stamp)
        if key not in _cache:
            _cache.clear()
            _cache[key] = _read_catalog(path)
        return _cache[key]


class ReportTexts:
    """某种语言的报告文字查表。"""

    def __init__(self, language: ReportLanguage, catalog: Catalog | None = None) -> None:
        """创建查表对象。

        Args:
            language: 报告语言。
            catalog: 译文表，默认从翻译文件加载；测试可注入。
        """
        self.language = language
        self._catalog = load_catalog(language) if catalog is None else catalog
        self._missing: set[tuple[str, str]] = set()

    @property
    def is_english(self) -> bool:
        """是否英文报告。"""
        return self.language is ReportLanguage.EN

    def tr(self, source: str, context: str = CONTEXT) -> str:
        """取译文。

        Args:
            source: 简体中文原文。
            context: 翻译上下文，默认报告专用上下文。

        Returns:
            当前语言的文字；简体中文返回原文，英文缺译文时回退原文。
        """
        if self.language is ReportLanguage.ZH_CN:
            return source
        text = self._catalog.get((context, source))
        if text is None:
            if (context, source) not in self._missing:
                self._missing.add((context, source))
                _log.warning("报告文字缺少英文译文：[%s] %s", context, source)
            return source
        return text

    def s(self, key: S, **params: object) -> str:
        """取报告里的一条固定文字。

        Args:
            key: 文字的键。
            **params: ``{参数}`` 的取值。

        Returns:
            当前语言的文字，已代入参数。
        """
        text = self.tr(SOURCES[key])
        return text.format(**params) if params else text

    @property
    def missing(self) -> frozenset[tuple[str, str]]:
        """查过但没有译文的（上下文，原文），测试用。"""
        return frozenset(self._missing)

    def number(self, value: float | None, *, blank: str = "–") -> str:
        """格式化数字，不带二进制浮点尾巴。

        Args:
            value: 数值。
            blank: 没有值时显示的文字。

        Returns:
            例如 ``0.5946``、``0.125``。
        """
        return blank if value is None else f"{value:.4g}"


def report_language_of(code: str) -> ReportLanguage:
    """把语言代码转成报告语言，不认识的按简体中文。

    Args:
        code: 语言代码，例如 ``en``、``zh_CN``。

    Returns:
        报告语言。
    """
    try:
        return ReportLanguage(code)
    except ValueError:
        return ReportLanguage.ZH_CN


__all__ = [
    "CONTEXT",
    "Catalog",
    "ReportLanguage",
    "ReportTexts",
    "load_catalog",
    "report_language_of",
]
