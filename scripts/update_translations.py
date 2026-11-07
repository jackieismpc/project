"""更新 ``translations/*.ts``：从源码提取所有 ``tr()`` 文字，保留已有译文。

用法::

    uv run python scripts/update_translations.py          # 更新 .ts
    uv run python scripts/update_translations.py --check  # 只检查，.ts 过期或有未翻译项时返回 1

源语言是简体中文（``zh_CN``），所以 ``gelinspect_zh_CN.ts`` 的译文留空，界面直接显示源文本；
``gelinspect_zh_TW.ts``（繁体中文，台湾用语）和 ``gelinspect_en.ts`` 的每一项都必须有译文。
``.qm`` 由 ``lrelease`` 在软件启动时自动生成，也可以手动运行
``uv run pyside6-lrelease translations/gelinspect_en.ts``。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import PySide6

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "src" / "gelinspect"
TRANSLATIONS = ROOT / "translations"
LANGUAGES = ("zh_CN", "zh_TW", "en")
SOURCE_LANGUAGE = "zh_CN"


def lupdate() -> Path:
    """找到 PySide6 自带的 ``lupdate``。"""
    package = Path(PySide6.__file__).resolve().parent
    for name in ("lupdate.exe", "lupdate"):
        if (package / name).is_file():
            return package / name
    raise SystemExit("找不到 lupdate，请先运行 uv sync")


def run_lupdate(targets: dict[str, Path]) -> None:
    """对全部源文件运行 lupdate，为每种语言生成或更新 .ts。"""
    files = sorted(str(p) for p in SOURCES.rglob("*.py"))
    for language, target in targets.items():
        command = [
            str(lupdate()),
            "-silent",
            "-no-obsolete",
            "-locations",
            "none",
            "-source-language",
            SOURCE_LANGUAGE,
            "-target-language",
            language,
            *files,
            "-ts",
            str(target),
        ]
        subprocess.run(command, check=True)


def messages(path: Path) -> dict[tuple[str, str], str | None]:
    """读取 .ts，返回 (上下文, 源文本) 到译文的映射；未完成或为空的译文为 None。"""
    result: dict[tuple[str, str], str | None] = {}
    for context in ET.parse(path).getroot().iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            source = message.findtext("source") or ""
            translation = message.find("translation")
            text = None
            if translation is not None and translation.get("type") != "unfinished":
                text = "".join(translation.itertext()) or None
            result[(name, source)] = text
    return result


def main() -> int:
    """更新或检查翻译文件。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只检查，不修改文件")
    args = parser.parse_args()
    TRANSLATIONS.mkdir(exist_ok=True)
    if not args.check:
        run_lupdate({lang: TRANSLATIONS / f"gelinspect_{lang}.ts" for lang in LANGUAGES})
        print("已更新", TRANSLATIONS)
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        fresh = {lang: Path(tmp) / f"{lang}.ts" for lang in LANGUAGES}
        run_lupdate(fresh)
        for lang in LANGUAGES:
            current = TRANSLATIONS / f"gelinspect_{lang}.ts"
            if not current.is_file():
                problems.append(f"缺少 {current.name}")
                continue
            have, want = messages(current), messages(fresh[lang])
            problems += [f"{current.name} 缺少：{k}" for k in want.keys() - have.keys()]
            problems += [f"{current.name} 多余：{k}" for k in have.keys() - want.keys()]
            if lang != SOURCE_LANGUAGE:
                problems += [f"{current.name} 未翻译：{k}" for k, v in have.items() if v is None]
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
