"""运行 ``docs/algorithm-interface.md`` 中的最小示例，防止文档与接口不一致。"""

import re
from pathlib import Path
from typing import Any

from gelinspect.interfaces.analysis import GelAnalyzer, check_row_result
from tests.contract.conftest import RequestFactory

DOC = Path(__file__).parents[2] / "docs" / "algorithm-interface.md"


def load_example() -> dict[str, Any]:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r'```python\n("""GelAnalyzer 最小示例.*?)```', text, flags=re.DOTALL)
    assert match, "文档中找不到最小示例"
    namespace: dict[str, Any] = {"__name__": "doc_example"}
    exec(compile(match.group(1), str(DOC), "exec"), namespace)
    return namespace


def test_doc_example_satisfies_contract(make_request: RequestFactory) -> None:
    analyzer = load_example()["MinimalAnalyzer"]()
    assert isinstance(analyzer, GelAnalyzer)
    analyzer.load(None)
    request = make_request()
    assert check_row_result(request, analyzer.analyze_row(request)) == []
    analyzer.close()
