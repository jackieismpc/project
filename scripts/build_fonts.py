r"""重新裁剪内置的 Noto Sans SC 字体（开发期工具，结果提交进仓库，运行软件不需要它）。

字符集（取并集）：

* 现有三个字体已有的全部字符（GB2312 汉字、拉丁、希腊、标点等，保证简体界面不变）；
* GB2312 全部汉字；
* Big5 一级字（5401 个台湾常用繁体字，覆盖教育部常用国字标准字体表的 4808 字，见
  ``docs/features/auth-and-i18n.md``）；
* 界面翻译（三种语言的原文和译文）、报告模板和源码里实际出现的全部汉字与全角标点；
* 全角符号区（``U+3000``–``U+303F``、``U+FF00``–``U+FFEF``）和注音符号。

方法：从 google/fonts 仓库的可变字体 ``NotoSansSC[wght].ttf`` 先裁剪再固定字重（400、500、700），
名称表沿用现有文件（族名 ``Noto Sans SC``，三个字重可同时注册）。fontTools 只在这里用到，
不是项目依赖::

    curl -L -o "$TMPDIR/NotoSansSC-VF.ttf" \\
      "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
    uv run --with fonttools python scripts/build_fonts.py "$TMPDIR/NotoSansSC-VF.ttf"
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "src" / "gelinspect" / "ui" / "assets" / "fonts"
WEIGHTS = {"Regular": 400, "Medium": 500, "Bold": 700}
HAN = re.compile(r"[㐀-䶿一-鿿]")


def gb2312_hanzi() -> set[int]:
    """GB2312 的全部汉字（一、二级共 6763 个）。"""
    chars: set[int] = set()
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(ord(bytes([high, low]).decode("gb2312")))
            except UnicodeDecodeError:
                continue
    return chars


def big5_level1_hanzi() -> set[int]:
    """Big5 一级字（``A440``–``C67E``，5401 个台湾常用繁体字）。"""
    chars: set[int] = set()
    for high in range(0xA4, 0xC7):
        for low in [*range(0x40, 0x7F), *range(0xA1, 0xFF)]:
            code = bytes([high, low])
            if code > bytes([0xC6, 0x7E]):
                continue
            try:
                chars.add(ord(code.decode("big5")))
            except UnicodeDecodeError:
                continue
    return chars


def used_in_project() -> set[int]:
    """翻译文件和源码里实际出现的汉字与标点。"""
    found: set[int] = set()
    pattern = re.compile(r"[ -⁯　-〿㐀-鿿＀-￯]")
    for path in (ROOT / "translations").glob("*.ts"):
        found.update(ord(c) for c in pattern.findall(path.read_text(encoding="utf-8")))
    for folder in ("src", "simulation"):
        for path in (ROOT / folder).rglob("*.py"):
            found.update(ord(c) for c in pattern.findall(path.read_text(encoding="utf-8")))
    # 译文再走一遍 XML，确保实体转义后的字符也在
    for path in (ROOT / "translations").glob("*.ts"):
        for node in ET.parse(path).getroot().iter():
            found.update(ord(c) for c in pattern.findall(node.text or ""))
    return found


def target_codepoints() -> set[int]:
    """裁剪后字体应包含的全部码位。"""
    chars: set[int] = set()
    for name in WEIGHTS:
        chars.update(TTFont(FONT_DIR / f"NotoSansSC-{name}.ttf").getBestCmap())
    chars |= gb2312_hanzi() | big5_level1_hanzi() | used_in_project()
    chars.update(range(0x3000, 0x3040))
    chars.update(range(0xFF00, 0xFFF0))
    chars.update(range(0x3100, 0x3130))  # 注音符号
    chars.update((0x02C7, 0x02C9, 0x02CA, 0x02CB, 0x02D9))  # 注音声调
    # 报告里的“+ 阳性，− 阴性”用到数学减号；常用数学和箭头符号一并带上
    chars.update((0x00B1, 0x00D7, 0x00F7, 0x2212, 0x2248, 0x2260, 0x2264, 0x2265))
    chars.update(range(0x2190, 0x2194))
    return chars


def build(variable: Path, output: Path) -> None:
    """裁剪并固定字重，写出三个静态字体。"""
    chars = target_codepoints()
    source = TTFont(variable)
    cmap = source.getBestCmap()
    wanted = sorted(c for c in chars if c in cmap)
    missing = sorted(c for c in chars if c not in cmap and HAN.match(chr(c)))
    print(f"目标码位 {len(chars)}，字体里有 {len(wanted)}，汉字缺失 {len(missing)}")
    if missing:
        print("字体里没有的汉字：", "".join(chr(c) for c in missing[:50]), file=sys.stderr)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.glyph_names = False
    options.hinting = True
    options.drop_tables += ["DSIG"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=wanted)
    subsetter.subset(source)
    output.mkdir(parents=True, exist_ok=True)
    subset_path = output / "_subset-vf.ttf"
    source.save(subset_path)
    for style, weight in WEIGHTS.items():
        font = instancer.instantiateVariableFont(TTFont(subset_path), {"wght": weight})
        old = TTFont(FONT_DIR / f"NotoSansSC-{style}.ttf")
        font["name"] = old["name"]  # 沿用现有名称表，三个字重可同时注册
        font["OS/2"].usWeightClass = weight
        target = output / f"NotoSansSC-{style}.ttf"
        font.save(target)
        size_kb = target.stat().st_size / 1024
        print(f"{target.name}: {size_kb:.0f} KB，{len(font.getBestCmap())} 个码位")
    subset_path.unlink()


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("variable", type=Path, help="NotoSansSC[wght].ttf 的路径")
    parser.add_argument("--output", type=Path, default=FONT_DIR, help="输出目录，默认覆盖内置字体")
    args = parser.parse_args()
    build(args.variable, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
