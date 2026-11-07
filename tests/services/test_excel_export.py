"""Excel 导出：内容、中文和文件名安全、公式注入防护、权限、审计。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, ExperimentType, Role
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest
from gelinspect.services import export_service as export_module
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.excel_export import (
    EXPORT_TEXTS,
    ExportLabels,
    safe_filename,
    safe_sheet_title,
    sanitize_cell,
)
from gelinspect.services.export_service import ExportService, suggested_filename
from tests.services.reporting.conftest import make_completed

from .conftest import NOW, Stack

PAYLOADS = ["=1+1", "+1+1", "-2+3", "@SUM(A1)", '=HYPERLINK("http://x","点击")', "\t=cmd", "\r=cmd"]


@pytest.fixture
def exports(stack: Stack) -> ExportService:
    return ExportService(stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=True)


def test_formula_like_text_is_escaped_and_other_values_are_untouched() -> None:
    for text in PAYLOADS:
        escaped = sanitize_cell(text)
        assert isinstance(escaped, str) and escaped.startswith("'") and escaped[1:] == text
    for harmless in ("样品 A", "x=1", "abc-1", "", "0.5", "1 + 1"):
        assert sanitize_cell(harmless) == harmless
    assert sanitize_cell(-5) == -5 and sanitize_cell(1.5) == 1.5 and sanitize_cell(None) is None
    assert sanitize_cell("a\x00b\x07c") == "abc"  # Excel 不允许的控制字符
    assert len(str(sanitize_cell("x" * 40000))) <= 32000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("报告 2026/11/06", "报告 2026_11_06.xlsx"),
        ("../../etc/passwd", "etc_passwd.xlsx"),
        ('a<b>c:d"e|f?g*h', "a_b_c_d_e_f_g_h.xlsx"),
        ("CON", "_CON.xlsx"),
        ("lpt1.txt", "_lpt1.txt.xlsx"),
        ("  ...  ", "export.xlsx"),
        ("ok.xlsx", "ok.xlsx"),
        ("tab\tname\n", "tab_name.xlsx"),
    ],
)
def test_filenames_are_safe_on_every_platform(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected
    name = safe_filename("长" * 300)
    assert len(name) <= 100 and name.endswith(".xlsx")


def test_sheet_titles_survive_hostile_translations() -> None:
    assert safe_sheet_title("[审计]:追踪/日志?*") == "_审计__追踪_日志__"
    assert safe_sheet_title("x" * 60) == "x" * 31
    assert safe_sheet_title("  ") == "Sheet"


def test_suggested_filename_is_safe_and_timestamped() -> None:
    name = suggested_filename("results-EXP/../1", now=NOW)
    assert name.endswith(".xlsx") and "/" not in name and ".." not in name


def dirty_experiment(stack: Stack, experiment_id: str = "EXP-X-0001") -> None:
    """已完成的试验，样品名称和批号里塞进公式。"""
    experiment = make_completed(stack, experiment_id=experiment_id)
    experiment.assignments = tuple(
        replace(a, sample=replace(a.sample, name=PAYLOADS[i % len(PAYLOADS)], batch_no="批 2026"))
        for i, a in enumerate(experiment.assignments)
    )
    experiment.mvd, experiment.endotoxin_limit = 1280.0, 0.25
    stack.database.experiments.save(experiment)


def test_results_workbook_has_conclusion_lambda_c_features_and_review(
    stack: Stack, exports: ExportService, tmp_path: Path
) -> None:
    dirty_experiment(stack)
    stored = stack.database.experiments.get("EXP-X-0001")
    assert stored is not None and stored.conclusion is not None
    target = tmp_path / "out" / "结果.xlsx"
    summary = exports.export_results(stack.login("analyst"), ["EXP-X-0001"], target)
    assert summary.path == target and summary.rows == 17 and target.is_file()
    assert not list(target.parent.glob(".export-*"))  # 临时文件已清理

    book = load_workbook(target)
    assert book.sheetnames == ["试验", "单管结果", "结论依据"]
    head = [c.value for c in book["试验"][1]]
    row = dict(zip(head, [c.value for c in book["试验"][2]], strict=True))
    assert row["试验编号"] == "EXP-X-0001" and row["管数"] == 17
    assert row["结论"] == stored.conclusion.verdict.value
    assert row["结论数值"] == pytest.approx(stored.conclusion.value)  # λc
    assert row["实测灵敏度 λc（EU/mL）"] == pytest.approx(stored.measured_sensitivity)
    assert row["MVD"] == 1280 and row["内毒素限值"] == 0.25
    assert row["鲎试剂批号"] == "LAL2606A" and row["规则版本"] == stored.conclusion.rule_version
    assert row["标示灵敏度 λ（EU/mL）"] == 0.5

    tubes = book["单管结果"]
    columns = [c.value for c in tubes[1]]
    first = dict(zip(columns, [c.value for c in tubes[2]], strict=True))
    assert tubes.max_row == 18
    for needed in ("管号", "排", "样品名称", "最终判读", "复核状态", "复核人", "复核备注",
                   "面积变化率 ΔA（%）", "轴向偏移 Δs", "模型版本", "配置版本"):  # fmt: skip
        assert needed in columns
    reviewed = [
        dict(zip(columns, [c.value for c in r], strict=True))
        for r in tubes.iter_rows(min_row=2)
        if r[columns.index("复核状态")].value == "reviewed"
    ]
    assert reviewed and all(r["复核人"] == "analyst" and r["复核备注"] == "复核" for r in reviewed)
    assert first["管号"] == 1 and first["排"] == "Row01"
    assert book["结论依据"].max_row >= 2  # 有依据行


def test_every_user_text_is_neutralised_in_the_file(
    stack: Stack, exports: ExportService, tmp_path: Path
) -> None:
    dirty_experiment(stack)
    target = tmp_path / "x.xlsx"
    exports.export_results(stack.login("admin"), ["EXP-X-0001"], target)
    sheet = load_workbook(target)["单管结果"]
    names = [row[3] for row in sheet.iter_rows(min_row=2)]
    assert len(names) == 17
    for cell in names:
        assert cell.data_type == "s", cell.value  # 绝不是公式
        assert str(cell.value).startswith("'") or not str(cell.value).startswith(
            ("=", "+", "-", "@")
        )
    assert {str(c.value).lstrip("'") for c in names} <= set(PAYLOADS)
    # 没有任何单元格是公式
    for book_sheet in load_workbook(target):
        assert not any(c.data_type == "f" for row in book_sheet.iter_rows() for c in row)


def test_headers_follow_the_label_translation_and_cover_every_export_text(
    stack: Stack, exports: ExportService, tmp_path: Path
) -> None:
    dirty_experiment(stack)
    used: set[str] = set()

    def record(text: str) -> str:
        used.add(text)
        return f"[{text}]"

    labels = ExportLabels(text=record)
    admin = stack.login("admin")
    make_completed(stack, experiment_id="EXP-X-0002", layout_confirmed=False)  # 有未满足的前提
    exports.export_results(admin, ["EXP-X-0001", "EXP-X-0002"], tmp_path / "r.xlsx", labels)
    exports.export_history(admin, ExperimentQuery(), tmp_path / "h.xlsx", labels)
    exports.export_audit(admin, AuditFilter(), tmp_path / "a.xlsx", labels)
    assert used == set(EXPORT_TEXTS)  # 服务层用到的文字都登记了，界面才会翻译
    headers = [c.value for c in load_workbook(tmp_path / "h.xlsx")["_历史列表_"][1]]
    assert headers[0] == "[试验编号]"


def test_history_export_follows_the_filter_across_pages(
    stack: Stack, exports: ExportService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_module, "_PAGE", 2)
    for n in range(5):
        experiment = make_completed(stack, experiment_id=f"EXP-H-{n}")
        if n >= 3:
            experiment.experiment_type = ExperimentType.LIMIT
            stack.database.experiments.save(experiment)
    admin = stack.login("admin")
    everything = exports.export_history(admin, ExperimentQuery(), tmp_path / "all.xlsx")
    assert everything.rows == 5
    only = exports.export_history(
        admin, ExperimentQuery(experiment_type=ExperimentType.LIMIT), tmp_path / "limit.xlsx"
    )
    assert only.rows == 2
    rows = list(
        load_workbook(tmp_path / "limit.xlsx")["历史列表"].iter_rows(min_row=2, values_only=True)
    )
    assert {r[0] for r in rows} == {"EXP-H-3", "EXP-H-4"} and {r[1] for r in rows} == {"limit"}
    with pytest.raises(ServiceError) as caught:
        exports.export_history(
            admin, ExperimentQuery(batch_no="没有这个批号"), tmp_path / "none.xlsx"
        )
    assert caught.value.code is ServiceErrorCode.EXPORT_EMPTY
    assert not (tmp_path / "none.xlsx").exists()


def test_audit_export_has_the_page_columns_and_escapes_details(
    stack: Stack, exports: ExportService, tmp_path: Path
) -> None:
    stack.database.audit.append(
        AuditEvent(NOW, "alice", Role.ANALYST, AuditAction.LOGIN, "=cmd|' /C calc'!A0", "EXP-1")
    )
    summary = exports.export_audit(
        stack.login("analyst"), AuditFilter(keyword="calc"), tmp_path / "a.xlsx"
    )
    assert summary.rows == 1
    sheet = load_workbook(tmp_path / "a.xlsx")["审计追踪"]
    head = [c.value for c in sheet[1]]
    assert head == ["序号", "时间", "操作者", "权限", "事件类型", "操作记录", "关联试验", "哈希"]
    row = dict(zip(head, sheet[2], strict=True))
    assert row["操作者"].value == "alice" and row["权限"].value == "analyst"
    assert row["操作记录"].value.startswith("'=") and row["操作记录"].data_type == "s"
    assert len(row["哈希"].value) == 64


def test_export_permissions_and_audit(stack: Stack, exports: ExportService, tmp_path: Path) -> None:
    dirty_experiment(stack)
    experimenter = stack.login("experimenter")  # 没有 data_export
    calls: list[Callable[[], object]] = [
        lambda: exports.export_results(experimenter, ["EXP-X-0001"], tmp_path / "1.xlsx"),
        lambda: exports.export_history(experimenter, ExperimentQuery(), tmp_path / "2.xlsx"),
        lambda: exports.export_audit(experimenter, AuditFilter(), tmp_path / "3.xlsx"),
    ]
    for call in calls:
        with pytest.raises(PermissionDeniedError):
            call()
    assert not list(tmp_path.glob("*.xlsx"))
    with pytest.raises(PermissionDeniedError):
        exports.export_results(None, ["EXP-X-0001"], tmp_path / "4.xlsx")
    analyst = stack.login("analyst")
    exports.export_results(analyst, ["EXP-X-0001"], tmp_path / "ok.xlsx")
    exports.export_audit(stack.login("admin"), AuditFilter(), tmp_path / "ok2.xlsx")
    lines = stack.audit(AuditAction.DATA_EXPORTED)
    assert any("导出试验结果到 Excel：1 个试验，17 管" in line for line in lines)
    assert any("导出审计追踪到 Excel" in line for line in lines)


def test_export_failures_are_reported_and_leave_nothing_behind(
    stack: Stack, exports: ExportService, tmp_path: Path
) -> None:
    dirty_experiment(stack)
    admin = stack.login("admin")
    with pytest.raises(ServiceError) as empty:
        exports.export_results(admin, [], tmp_path / "e.xlsx")
    assert empty.value.code is ServiceErrorCode.EXPORT_EMPTY
    with pytest.raises(ServiceError) as missing:
        exports.export_results(admin, ["EXP-NOPE"], tmp_path / "e.xlsx")
    assert missing.value.code is ServiceErrorCode.EXPERIMENT_NOT_FOUND
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ServiceError) as unwritable:
        exports.export_results(admin, ["EXP-X-0001"], blocker / "sub" / "x.xlsx")
    assert unwritable.value.code is ServiceErrorCode.EXPORT_FAILED
    odd = exports.export_results(admin, ["EXP-X-0001"], tmp_path / "no_extension")
    assert odd.path.name == "no_extension.xlsx"  # 缺扩展名时补上
    assert ExperimentState.COMPLETED  # 导入使用


def test_audit_export_walks_every_page_without_gaps_or_repeats(
    stack: Stack, exports: ExportService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_module, "_PAGE", 7)
    stack.database.audit.append_many(
        [
            AuditEvent(
                NOW, "alice" if i % 2 else "bob", Role.ANALYST, AuditAction.LOGIN, f"事件 {i}"
            )
            for i in range(30)
        ]
    )
    admin = stack.login("admin")
    summary = exports.export_audit(admin, AuditFilter(actor="bob"), tmp_path / "bob.xlsx")
    rows = list(
        load_workbook(tmp_path / "bob.xlsx")["审计追踪"].iter_rows(min_row=2, values_only=True)
    )
    seqs = [r[0] for r in rows]
    assert summary.rows == len(rows) == 15 and len(set(seqs)) == len(seqs)
    assert seqs == sorted(seqs, reverse=True) and {r[2] for r in rows} == {"bob"}
    total = stack.database.audit.query(AuditFilter().to_query(), PageRequest(limit=1)).total
    every = exports.export_audit(admin, AuditFilter(), tmp_path / "all.xlsx")
    assert every.rows == total  # 导出开始前已有的记录一条不少
