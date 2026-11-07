"""界面语言：简体中文（源语言）、繁体中文（台湾用语）和英文。

* 源文本全部是简体中文，繁体中文翻译在 ``translations/gelinspect_zh_TW.ts``，英文翻译在
  ``translations/gelinspect_en.ts``。
* ``.qm`` 由 ``.ts`` 编译而来，不纳入版本库。:func:`compile_translations` 在启动时检查，
  缺失或比 ``.ts`` 旧就调用 ``lrelease`` 重新生成，所以干净克隆后直接运行就能切换语言。
* :class:`LanguageManager` 安装或移除 ``QTranslator``，Qt 随即向所有控件发出
  ``LanguageChange`` 事件，控件在 ``changeEvent`` 里刷新文字。选择保存在 ``QSettings``。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import PySide6
from PySide6.QtCore import QLibraryInfo, QLocale, QObject, QSettings, QTranslator, Signal
from PySide6.QtWidgets import QApplication

from gelinspect.services.runtime_paths import RunMode, run_mode
from gelinspect.services.runtime_paths import translations_dir as _translations_dir

_log = logging.getLogger(__name__)

TRANSLATION_PREFIX = "gelinspect_"
"""翻译文件名前缀，完整名称如 ``gelinspect_en.ts``。"""

_SETTINGS_KEY = "ui/language"


class Language(StrEnum):
    """界面语言。值是文件名里的语言代码。"""

    ZH_CN = "zh_CN"
    """简体中文，源语言。"""
    ZH_TW = "zh_TW"
    """繁体中文（台湾用语）。"""
    EN = "en"
    """英文。"""


SOURCE_LANGUAGE = Language.ZH_CN
"""源文本所用的语言，不需要翻译文件。"""

LANGUAGE_NAMES: dict[Language, str] = {
    Language.ZH_CN: "简体中文",
    Language.ZH_TW: "繁體中文",
    Language.EN: "English",
}
"""语言自己的名字，在语言菜单里原样显示，不翻译。"""

_TRADITIONAL_REGIONS = frozenset({"TW", "HK", "MO"})


def detect_system_language(locale: QLocale | None = None) -> Language | None:
    """根据系统语言挑选默认界面语言：系统是繁体中文时用繁体中文。

    其余情况返回 None，由调用方沿用源语言（简体中文）。

    Args:
        locale: 要判断的区域设置，默认 ``QLocale.system()``。

    Returns:
        :attr:`Language.ZH_TW` 或 None。
    """
    locale = locale or QLocale.system()
    if locale.language() != QLocale.Language.Chinese:
        return None
    if locale.script() == QLocale.Script.TraditionalHanScript:
        return Language.ZH_TW
    if locale.script() == QLocale.Script.SimplifiedHanScript:
        return None
    # 没写文字（如 zh_TW）时按地区判断：台湾、香港、澳门用繁体。
    region = locale.name().rsplit("_", 1)[-1].upper()
    return Language.ZH_TW if region in _TRADITIONAL_REGIONS else None


def translations_dir() -> Path:
    """翻译文件目录：仓库根目录（发布包是程序目录）下的 ``translations/``。

    Returns:
        目录路径。
    """
    return _translations_dir()


def ts_path(language: Language, directory: Path | None = None) -> Path:
    """某语言的 ``.ts`` 文件路径。

    Args:
        language: 语言。
        directory: 翻译目录，默认 :func:`translations_dir`。

    Returns:
        文件路径。
    """
    return (directory or translations_dir()) / f"{TRANSLATION_PREFIX}{language.value}.ts"


def find_lrelease() -> Path | None:
    """查找 ``lrelease``：优先 PySide6 包内自带的，其次 PATH 里的 ``pyside6-lrelease``。

    Returns:
        可执行文件路径，找不到时为 None。
    """
    package = Path(PySide6.__file__).resolve().parent
    for name in ("lrelease.exe", "lrelease"):
        candidate = package / name
        if candidate.is_file():
            return candidate
    found = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    return Path(found) if found else None


def _is_stale(ts: Path, qm: Path) -> bool:
    return not qm.is_file() or qm.stat().st_mtime < ts.stat().st_mtime


def compile_translations(
    directory: Path | None = None, *, languages: Sequence[Language] = tuple(Language)
) -> dict[Language, Path]:
    """把缺失或过期的 ``.ts`` 编译成 ``.qm``。

    ``.qm`` 优先放在翻译目录里（已被 git 忽略）；目录不可写时放到系统临时目录。
    源语言不需要编译。编译失败只记录日志，界面会回退显示源文本（简体中文）。

    Args:
        directory: 翻译目录，默认 :func:`translations_dir`。
        languages: 要处理的语言。

    Returns:
        成功得到 ``.qm`` 的语言及其路径。
    """
    source_dir = directory or translations_dir()
    result: dict[Language, Path] = {}
    for language in languages:
        if language is SOURCE_LANGUAGE:
            continue
        ts = ts_path(language, source_dir)
        if not ts.is_file():
            _log.warning("缺少翻译文件：%s", ts)
            continue
        qm = ts.with_suffix(".qm")
        # 发布包里的 .qm 是构建时生成的，包内没有 lrelease，也不应改动程序目录。
        if not _is_stale(ts, qm) or (qm.is_file() and run_mode() is RunMode.PACKAGED):
            result[language] = qm
            continue
        built = _run_lrelease(ts, qm) or _run_lrelease(
            ts, Path(tempfile.gettempdir()) / "gelinspect-i18n" / qm.name
        )
        if built is not None:
            result[language] = built
    return result


def _run_lrelease(ts: Path, qm: Path) -> Path | None:
    tool = find_lrelease()
    if tool is None:
        _log.warning("找不到 lrelease，无法编译翻译（请运行 uv sync）")
        return None
    try:
        qm.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [str(tool), "-silent", str(ts), "-qm", str(qm)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("编译翻译失败：%s", exc)
        return None
    if completed.returncode != 0 or not qm.is_file():
        _log.warning("编译翻译失败：%s %s", completed.stdout.strip(), completed.stderr.strip())
        return None
    return qm


class LanguageSettings(Protocol):
    """语言选择的持久化接口。"""

    def load_language(self) -> Language | None:
        """读取已保存的语言。

        Returns:
            语言；没有或无法识别时为 None。
        """
        ...

    def save_language(self, language: Language) -> None:
        """保存语言。

        Args:
            language: 要保存的语言。
        """
        ...


class MemoryLanguageSettings:
    """内存版，用于测试、截图和冒烟测试，不写磁盘。"""

    def __init__(self, language: Language | None = None) -> None:
        """创建设置。

        Args:
            language: 初始保存的语言。
        """
        self._language = language

    def load_language(self) -> Language | None:
        """读取语言。

        Returns:
            已保存的语言或 None。
        """
        return self._language

    def save_language(self, language: Language) -> None:
        """保存语言。

        Args:
            language: 要保存的语言。
        """
        self._language = language


class QSettingsLanguageSettings:
    """基于 ``QSettings`` 的持久化，应用默认使用。"""

    def __init__(self, settings: QSettings | None = None) -> None:
        """创建设置。

        Args:
            settings: ``QSettings`` 实例，默认使用组织 GelInspect、应用 GelInspect。
        """
        self._settings = settings or QSettings("GelInspect", "GelInspect")

    def load_language(self) -> Language | None:
        """读取语言。

        Returns:
            已保存的语言；没有或值非法时为 None。
        """
        raw = self._settings.value(_SETTINGS_KEY)
        try:
            return Language(str(raw))
        except ValueError:
            return None

    def save_language(self, language: Language) -> None:
        """保存语言并立即同步。

        Args:
            language: 要保存的语言。
        """
        self._settings.setValue(_SETTINGS_KEY, language.value)
        self._settings.sync()


class LanguageManager(QObject):
    """管理界面语言：安装翻译器、持久化选择。

    Signals:
        languageChanged: 语言已切换，参数为语言代码。
    """

    languageChanged = Signal(str)  # noqa: N815

    def __init__(
        self,
        app: QApplication,
        *,
        settings: LanguageSettings | None = None,
        directory: Path | None = None,
        compile_missing: bool = True,
        default_language: Language | None = None,
        parent: QObject | None = None,
    ) -> None:
        """创建管理器，并应用已保存的语言（没有保存过则用默认语言，再没有则为简体中文）。

        Args:
            app: 应用对象。
            settings: 持久化接口，默认 ``QSettings``。
            directory: 翻译目录，默认仓库根目录下的 ``translations/``。
            compile_missing: 是否在启动时编译缺失或过期的 ``.qm``。
            default_language: 没有保存过选择时采用的语言，例如 :func:`detect_system_language`
                的结果；为 None 时用源语言。
            parent: 父对象。
        """
        super().__init__(parent)
        self._app = app
        self._settings: LanguageSettings = settings or QSettingsLanguageSettings()
        self._directory = directory or translations_dir()
        self._compiled = (
            compile_translations(self._directory, languages=tuple(Language))
            if compile_missing
            else {}
        )
        self._translator: QTranslator | None = None
        self._qt_translator: QTranslator | None = None
        self._current = SOURCE_LANGUAGE
        initial = self._settings.load_language() or default_language or SOURCE_LANGUAGE
        if not self.set_language(initial, persist=False):
            self.set_language(SOURCE_LANGUAGE, persist=False)

    @property
    def current(self) -> Language:
        """当前语言。"""
        return self._current

    @property
    def compiled(self) -> dict[Language, Path]:
        """已编译好的翻译文件。"""
        return dict(self._compiled)

    def available(self) -> tuple[Language, ...]:
        """可以切换的语言：源语言加上有翻译文件的语言。

        Returns:
            语言元组。
        """
        return tuple(lang for lang in Language if lang is SOURCE_LANGUAGE or lang in self._compiled)

    def options(self) -> list[tuple[str, str]]:
        """语言菜单的选项。

        Returns:
            ``(语言代码, 语言自己的名字)`` 列表。
        """
        return [(lang.value, LANGUAGE_NAMES[lang]) for lang in self.available()]

    def set_language(self, language: Language | str, *, persist: bool = True) -> bool:
        """切换语言，立即对所有已打开的控件生效。

        Args:
            language: 语言或语言代码。
            persist: 是否保存选择。

        Returns:
            切换成功时为 True；翻译文件不可用时保持原语言并返回 False。
        """
        try:
            target = Language(language)
        except ValueError:
            return False
        if target not in self.available():
            _log.warning("语言 %s 的翻译不可用", target.value)
            return False
        if self._translator is not None:
            self._app.removeTranslator(self._translator)
            self._translator = None
        if self._qt_translator is not None:
            self._app.removeTranslator(self._qt_translator)
            self._qt_translator = None
        if target is not SOURCE_LANGUAGE:
            translator = QTranslator(self)
            if translator.load(str(self._compiled[target])):
                self._app.installTranslator(translator)
                self._translator = translator
            else:
                _log.warning("无法加载翻译文件 %s", self._compiled[target])
                return False
        if target is not Language.EN:
            self._install_qt_translations(target)
        self._current = target
        if persist:
            self._settings.save_language(target)
        self.languageChanged.emit(target.value)
        return True

    def _install_qt_translations(self, language: Language) -> None:
        """安装 Qt 自带的翻译（标准对话框、输入框右键菜单等）。"""
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator = QTranslator(self)
        if translator.load(f"qtbase_{language.value}", directory):
            self._app.installTranslator(translator)
            self._qt_translator = translator
