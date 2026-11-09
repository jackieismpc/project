"""给 ``translations/gelinspect_zh_TW.ts`` 里还没翻译的条目打繁体草稿（只在开发期用）。

OpenCC 不是项目依赖，临时借用::

    uv run --no-sync --with opencc-python-reimplemented python scripts/draft_traditional.py

做法：``OpenCC('s2twp')``（简体转台湾繁体并换用语）转换源文本，再把直角引号改回弯引号“”，按术语表
（``docs/features/auth-and-i18n.md``）替换 OpenCC 没覆盖或不统一的词。**只填 ``type="unfinished"``
或空的条目，已有译文（人工校对过的）不会被覆盖。** 草稿必须逐条人工校对后再提交。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

import opencc  # type: ignore[import-not-found,unused-ignore]

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "translations" / "gelinspect_zh_TW.ts"

PRE_REPLACEMENTS = (
    ("溯源码", "溯源碼"),  # OpenCC 会转成“溯原始碼”
    ("视频断流", "影片串流中斷"),
    ("丢帧", "掉影格"),
    ("验签", "驗簽"),  # OpenCC 会转成“驗籤”
    ("复位", "復位"),
)
"""转换前对简体源文本的替换。"""

POST_REPLACEMENTS = (
    ("審計追蹤", "稽核軌跡"),
    ("審計記錄", "稽核紀錄"),
    ("審計", "稽核"),
    ("告警", "警示"),
    ("質量", "品質"),
    ("影象", "影像"),
    ("幀率", "影格率"),
    ("抓幀", "擷取影格"),
    ("抓拍", "擷取"),
    ("幀", "影格"),
    ("許可權", "權限"),
    ("賬", "帳"),
    ("登入名", "登入名稱"),
    ("退出登入", "登出"),
    ("透過", "通過"),  # OpenCC 把“通过”转成“透過”
    ("校驗", "驗證"),
    ("記錄", "紀錄"),
    ("條紀錄", "筆紀錄"),
    ("型別", "類型"),
    ("置信度", "信賴度"),
    ("回撥", "回呼"),
    ("掩碼", "遮罩"),
    ("嚮導", "精靈"),
    ("後臺", "背景"),
    ("平臺", "平台"),
    ("臺", "台"),
    ("配置檔案", "設定檔"),
    ("配置", "設定"),
    ("當前", "目前"),
    ("實時", "即時"),
    ("列表", "清單"),
    ("掃碼槍", "掃描槍"),
    ("掃碼", "掃描"),
    ("錄制", "錄製"),
    ("錄入", "輸入"),
    ("演示", "示範"),
    ("示例", "範例"),
    ("自定義", "自訂"),
    ("釋出", "發行"),
    ("重連", "重新連線"),
    ("影片源", "影片來源"),
    ("丟失", "遺失"),
    ("丟棄", "捨棄"),
    ("匹配", "相符"),
    ("檢視", "查看"),
    ("庫裡", "資料庫裡"),
    ("庫內", "資料庫內"),
    ("入庫", "存入資料庫"),
    ("模板", "範本"),
    ("須改密", "須改密碼"),
    ("會話", "工作階段"),
    ("生成", "產生"),
    ("接入", "串接"),
    ("（可選）", "（選填）"),
    # 台湾用语（2026-11 审校）
    ("數字簽名", "數位簽章"),
    ("自簽證書", "自簽憑證"),
    ("簽名證書", "簽章憑證"),
    ("證書", "憑證"),
    ("已簽名", "已簽章"),
    ("簽名", "簽章"),
    ("驗簽", "驗章"),
    ("只讀", "唯讀"),
    ("新建", "新增"),
    ("重置", "重設"),
    ("導航", "導覽"),
    ("歸一化", "正規化"),
    ("實現", "實作"),
)
"""转换后对繁体文本的术语替换，顺序有关（长词在前）。"""


def convert(converter: opencc.OpenCC, source: str) -> str:
    """把一条简体源文本转成台湾繁体草稿。

    Args:
        converter: ``OpenCC('s2twp')``。
        source: 简体中文源文本。

    Returns:
        繁体草稿（弯引号 ``“”``，术语已替换，待人工校对）。
    """
    for old, new in PRE_REPLACEMENTS:
        source = source.replace(old, new)
    text = converter.convert(source)
    text = (
        text.replace("\u300c", "“")
        .replace("\u300d", "”")
        .replace("\u300e", "‘")
        .replace("\u300f", "’")
    )
    for old, new in POST_REPLACEMENTS:
        text = text.replace(old, new)
    text = re.sub(r"改密(?!碼)", "改密碼", text)
    text = re.sub(r"解壓(?!縮)", "解壓縮", text)
    text = re.sub(r"\s*回車\s*", " Enter ", text)
    text = re.sub(r" Enter ([，。、；：）])", r" Enter\1", text)
    return text.replace("（ Enter", "（Enter").replace("  ", " ")


def main() -> int:
    """填充未翻译的条目。"""
    converter = opencc.OpenCC("s2twp")
    root = ET.parse(TARGET).getroot()
    sources = [
        (message.findtext("source") or "")
        for message in root.iter("message")
        if (translation := message.find("translation")) is not None
        and (translation.get("type") == "unfinished" or not "".join(translation.itertext()))
    ]
    pending = iter(sources)
    text = TARGET.read_text(encoding="utf-8")

    def fill(match: re.Match[str]) -> str:
        draft = escape(convert(converter, next(pending)))
        return f"{match.group(1)}<translation>{draft}</translation>"

    text, count = re.subn(
        r'(<source>.*?</source>\s*)<translation type="unfinished"></translation>',
        fill,
        text,
        flags=re.S,
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"已填充 {count} 条草稿，请逐条人工校对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
