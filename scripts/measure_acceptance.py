"""验收指标的一次性测量：能在软件里量的项都实际跑一遍，打印数字（可选写 JSON）。

用法::

    QT_QPA_PLATFORM=offscreen uv run python scripts/measure_acceptance.py [--json out.json]

测量内容（依赖算法和样机的指标——8 支 10 秒、准确率、错判率——测不了，不在其中）：

1. 环境：系统、机型、Python、Qt。
2. 全流程：真实向导启动 17 管灵敏度复核，跑完后复核、签署、验签；再跑一次 12 排×8 支整板（96 管）。
   运行期间用 5 ms 的心跳定时器统计界面线程被阻塞的最长时间，作为“无卡顿”的依据。
3. 界面响应：10 万条审计加 1 万管结果的数据量下，逐页切换和切换语言的耗时。
4. 报告：预览（版面计算）、签署（渲染加签名加入库）、验签、批量导出的耗时。
5. 权限：全部角色乘全部权限，核对界面路由和服务层拒绝是否与权限矩阵一致。
6. 加密防护：私钥落盘不是明文、审计链篡改可检出、签名 PDF 改一个字节验签失败、
   导出加密、登录失败锁定、密码哈希。
7. 多语言：英文界面扫描中文残留、翻译文件是否完整。
8. 已有的 ``perf`` 测试（10 万条审计查询、Excel 导出、批量导出 100 份）。

耗时都是离屏（``QT_QPA_PLATFORM=offscreen``）下的墙钟时间，不含 GPU 绘制，见验收报告的说明。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import acceptance_support as support
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPageSize, QPdfWriter

from gelinspect import __version__
from gelinspect.domain.enums import AuditAction, ExperimentType, Interpretation, Role, TubeRole
from gelinspect.domain.permissions import PERMISSION_ROLES, Permission, has_permission
from gelinspect.domain.plate import ROW_COUNT, TUBE_COUNT
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.auth import AccountLockedError, AuthError, PermissionDeniedError
from gelinspect.services.config import DEFAULT_CONFIG
from gelinspect.services.encrypted_zip import read_encrypted_zip
from gelinspect.services.reporting.texts import ReportLanguage
from gelinspect.services.template_service import TemplateDraft, TemplateItemInput
from gelinspect.ui.i18n import Language
from gelinspect.ui.pages.new_experiment import NewExperimentPage
from gelinspect.ui.routes import PAGE_ROUTES, can_enter
from tests.infrastructure.db.conftest import T0, make_event, make_experiment
from tests.ui.monitor_helpers import wait_finished
from tests.ui.wizard_helpers import (
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    select_tubes,
)

ROOT = Path(__file__).resolve().parent.parent
RESULTS: dict[str, object] = {}


def log(text: str) -> None:
    """打印一行进度。"""
    print(text, flush=True)


def timed(call: Callable[[], object]) -> tuple[object, float]:
    """执行并返回（结果, 秒）。"""
    started = time.perf_counter()
    result = call()
    return result, time.perf_counter() - started


class Heartbeat:
    """每 5 ms 触发一次的定时器，记录相邻两次触发的间隔。

    界面线程被长时间占用（卡顿）时，间隔会明显变大；最大间隔就是用户感受到的最长冻结。
    """

    def __init__(self) -> None:
        """创建并启动。"""
        self.gaps_ms: list[float] = []
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        self.gaps_ms.append((now - self._last) * 1000)
        self._last = now

    def stop(self) -> dict[str, float]:
        """停止并返回统计。

        Returns:
            触发次数、中位数、第 99 百分位、最大间隔，以及超过 100/200 ms 的次数。
        """
        self._timer.stop()
        gaps = sorted(self.gaps_ms)
        if not gaps:
            return {"ticks": 0}
        return {
            "ticks": len(gaps),
            "median_ms": round(statistics.median(gaps), 1),
            "p99_ms": round(gaps[int(len(gaps) * 0.99) - 1], 1),
            "max_ms": round(gaps[-1], 1),
            "over_100ms": sum(1 for g in gaps if g > 100),
            "over_200ms": sum(1 for g in gaps if g > 200),
        }


def hardware_model() -> str:
    """机型（macOS 用 system_profiler，取不到时给平台信息）。"""
    if sys.platform == "darwin":
        try:
            text = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            ).stdout
            fields = dict(
                re.findall(r"^\s*(Model Name|Chip|Memory|Model Identifier):\s*(.+)$", text, re.M)
            )
            if fields:
                return " / ".join(
                    fields[k]
                    for k in ("Model Name", "Model Identifier", "Chip", "Memory")
                    if k in fields
                )
        except (OSError, subprocess.SubprocessError):
            pass
    return f"{platform.machine()} {platform.processor()}".strip()


PLATE_TEMPLATE = f"整板 {TUBE_COUNT} 管"
"""整板测量用的模板名：按领域层的管位总数生成。"""


def measure_environment() -> None:
    """记录环境。"""
    RESULTS["environment"] = {
        "system": f"{platform.system()} {platform.release()} ({platform.mac_ver()[0] or '-'})",
        "machine": hardware_model(),
        "python": platform.python_version(),
        "pyside6": pyside_version,
        "gelinspect": __version__,
        "qt_platform": "offscreen",
    }
    log(f"环境：{RESULTS['environment']}")


def prepare_wizard(rig: support.Rig, template: str, tubes: range) -> NewExperimentPage:
    """实验员走完向导的前三步（选模板、扫码、选管位），停在“确认启动”。

    向导由脚本用真实控件逐步点击，脚本自己的循环会占用线程，所以卡顿统计不含这一段。
    """
    app, window = rig.app, rig.window
    if window.session is not None:
        window.logout(confirm=False)
        support.settle(app)
    page = open_wizard(app, window, "experimenter")
    choose_template(app, page, template)
    click(app, page.template_next)
    pass_reagents(app, page)
    click(app, page.reagent_next)
    select_tubes(app, page, tubes)
    click(app, page.sample_next)
    return page


def start_and_wait(rig: support.Rig, page: NewExperimentPage) -> dict[str, object]:
    """点“确认并启动”，一直等到试验跑完；期间统计界面线程的阻塞，并切换监控页的标签。"""
    app, window = rig.app, rig.window
    beat = Heartbeat()
    started = time.perf_counter()
    click(app, page.confirm_start)
    assert support.wait_for(app, lambda: window.current_route == "monitor", 60.0)
    started_ui = time.perf_counter() - started
    monitor = window.page("monitor")
    tab_ms: list[float] = []
    for index in (1, 2, 3, 0, 1, 0):  # 运行中切换标签的响应
        began = time.perf_counter()
        monitor.tabs.set_current(index)  # type: ignore[attr-defined]
        monitor.stack.setCurrentIndex(index)  # type: ignore[attr-defined]
        support.settle(app, 3)
        tab_ms.append((time.perf_counter() - began) * 1000)
    wait_finished(app, rig.context, 300.0)
    elapsed = time.perf_counter() - started
    stats = beat.stop()
    stored = rig.context.database.experiments.get(monitor.experiment_id)  # type: ignore[attr-defined]
    assert stored is not None
    return {
        "experiment_id": monitor.experiment_id,  # type: ignore[attr-defined]
        "click_to_monitor_s": round(started_ui, 3),
        "start_to_finished_s": round(elapsed, 2),
        "tube_results": len(stored.results),
        "tab_switch_ms_max": round(max(tab_ms), 1),
        "ui_thread": stats,
    }


def measure_flow(root: Path) -> support.Rig:
    """17 管灵敏度复核的全流程（真实控件），再跑整板。返回仍打开的应用，供后面的测量使用。"""
    rig = support.open_rig(
        root,
        incubation_minutes=0.05,
        video=DEFAULT_CONFIG["video"],  # type: ignore[arg-type]
    )
    flow: dict[str, object] = {}
    began = time.perf_counter()
    page = prepare_wizard(rig, "灵敏度复核 (2λ:1)", range(1, 18))
    flow["wizard_s"] = round(time.perf_counter() - began, 2)
    flow.update(start_and_wait(rig, page))
    experiment_id = str(flow["experiment_id"])
    reviewed, review_s = timed(lambda: support.finish_and_review(rig, experiment_id))
    flow["reviewed_tubes"] = reviewed
    flow["review_s"] = round(review_s, 3)
    flow["sign_s"] = round(support.sign_experiment(rig, experiment_id), 3)
    analyst = rig.context.auth.login("analyst", support.DEMO_PASSWORDS["analyst"])
    record = rig.context.reports.signed_reports(analyst)[0]
    verification, verify_s = timed(
        lambda: rig.context.reports.verify_report(analyst, record.report_id)
    )
    flow["verify_s"] = round(verify_s, 3)
    flow["verify_ok"] = bool(verification.ok)  # type: ignore[attr-defined]
    flow["total_s"] = round(time.perf_counter() - began, 2)
    RESULTS["flow_17_tubes"] = flow
    log(f"全流程（17 管）：{flow}")

    admin = rig.context.auth.login("admin", support.DEMO_PASSWORDS["admin"])
    rig.context.templates.create_template(
        admin,
        TemplateDraft(
            PLATE_TEMPLATE,
            ExperimentType.INTERFERENCE,
            [TemplateItemInput(f"整板样品 {n}", TubeRole.TEST) for n in range(1, TUBE_COUNT + 1)],
        ),
    )
    began = time.perf_counter()
    page = prepare_wizard(rig, PLATE_TEMPLATE, range(1, TUBE_COUNT + 1))
    plate: dict[str, object] = {
        "tubes": TUBE_COUNT,
        "rows": ROW_COUNT,
        "wizard_s": round(time.perf_counter() - began, 2),
    }
    plate.update(start_and_wait(rig, page))
    RESULTS["flow_96_tubes"] = plate
    log(f"整板（{TUBE_COUNT} 管）：{plate}")
    return rig


def seed_big_data(rig: support.Rig) -> None:
    """写入 10 万条审计和 125×80=1 万管结果（与 perf 测试同一规模）。"""
    audit = rig.context.database.audit
    actions = list(AuditAction)
    started = time.perf_counter()
    for start in range(0, 100_000, 10_000):
        audit.append_many(
            [
                make_event(
                    i,
                    actor=f"user{i % 20:02d}",
                    action=actions[i % len(actions)],
                    detail=f"记录 {i} 批号 LAL-{i % 500:03d}",
                    experiment_id=f"E{i % 125:03d}",
                )
                for i in range(start, start + 10_000)
            ]
        )
    for n in range(125):
        experiment = make_experiment(
            f"E{n:03d}",
            experiment_type=list(ExperimentType)[n % 5],
            created_at=T0 + timedelta(hours=n),
            tube_count=80,
            batch_no=f"LAL-{n % 25:03d}",
        )
        experiment.operator = "experimenter" if n % 2 else "admin"
        experiment.state = [ExperimentState.SIGNED, ExperimentState.COMPLETED][n % 2]
        rig.context.database.experiments.save(experiment)
    log(f"造数据：10 万条审计 + 1 万管结果 {time.perf_counter() - started:.1f}s")


def measure_ui_response(rig: support.Rig) -> None:
    """大数据量下逐页切换、查询和切换语言的耗时。"""
    app, window = rig.app, rig.window
    if window.session is not None:
        window.logout(confirm=False)
    support.sign_in(app, window, "admin")
    first: dict[str, float] = {}
    again: dict[str, float] = {}
    for route in PAGE_ROUTES:
        if route.key == "tube":
            continue
        _, seconds = timed(lambda key=route.key: window.navigate(key))  # type: ignore[misc]
        support.settle(app, 6)
        first[route.key] = round(seconds * 1000, 1)
    for route in PAGE_ROUTES:
        if route.key == "tube":
            continue
        began = time.perf_counter()
        window.navigate(route.key)
        support.wait_for(app, lambda: False, 0.3)  # 等后台线程把数据读出来
        again[route.key] = round((time.perf_counter() - began - 0.3) * 1000, 1)
    # 页面上的查询：审计页按关键字（全表子串）、历史页按批号
    window.navigate("audit")
    audit = window.page("audit")
    audit.keyword_edit.setText("LAL-123")  # type: ignore[attr-defined]
    began = time.perf_counter()
    audit.search_button.click()  # type: ignore[attr-defined]
    support.wait_for(app, lambda: not audit.loading, 5.0)  # type: ignore[attr-defined]
    audit_search_ms = (time.perf_counter() - began) * 1000
    # 语言切换：全部页面都创建过之后
    began = time.perf_counter()
    rig.language.set_language(Language.EN, persist=False)
    support.settle(app, 6)
    to_en = time.perf_counter() - began
    began = time.perf_counter()
    rig.language.set_language(Language.ZH_CN, persist=False)
    support.settle(app, 6)
    to_zh = time.perf_counter() - began
    RESULTS["ui_response"] = {
        "navigate_first_ms": first,
        "navigate_first_max_ms": max(first.values()),
        "navigate_again_ms": again,
        "navigate_again_max_ms": max(again.values()),
        "audit_keyword_search_ms": round(audit_search_ms, 1),
        "language_switch_zh_to_en_ms": round(to_en * 1000, 1),
        "language_switch_en_to_zh_ms": round(to_zh * 1000, 1),
    }
    log(f"界面响应：{RESULTS['ui_response']}")


def measure_queries_and_export(rig: support.Rig, tmp: Path) -> None:
    """服务层查询和导出。"""
    admin = rig.context.auth.login("admin", support.DEMO_PASSWORDS["admin"])
    audit, queries, exports = rig.context.audit, rig.context.queries, rig.context.exports
    out: dict[str, float] = {}

    def run(label: str, call: Callable[[], object]) -> None:
        _, seconds = timed(call)
        out[label] = round(seconds * 1000, 1)

    run("审计 首页", lambda: audit.query(admin, AuditFilter(), PageRequest(limit=50)))
    run(
        "审计 深翻页 offset=50000",
        lambda: audit.query(
            admin, AuditFilter(max_seq=100_000), PageRequest(offset=50_000, limit=50)
        ),
    )
    run(
        "审计 按关键字",
        lambda: audit.query(admin, AuditFilter(keyword="LAL-123"), PageRequest(limit=50)),
    )
    run(
        "历史 首页",
        lambda: queries.search_experiments(admin, ExperimentQuery(), PageRequest(limit=20)),
    )
    run(
        "历史 按批号",
        lambda: queries.search_experiments(
            admin, ExperimentQuery(batch_no="LAL-007"), PageRequest(limit=20)
        ),
    )
    run("校验 10 万+ 条哈希链", lambda: audit.verify_chain(admin))
    ids = [f"E{n:03d}" for n in range(125)]
    summary, seconds = timed(lambda: exports.export_results(admin, ids, tmp / "results.xlsx"))
    out["Excel 导出 1 万管结果 (ms)"] = round(seconds * 1000, 1)
    out["Excel 行数"] = summary.rows  # type: ignore[attr-defined]
    RESULTS["queries"] = out
    log(f"查询与导出：{out}")


def measure_reports(rig: support.Rig, tmp: Path) -> None:
    """报告：预览、签署、验签、导出。"""
    analyst = rig.context.auth.login("analyst", support.DEMO_PASSWORDS["analyst"])
    reports = rig.context.reports
    flow17 = RESULTS["flow_17_tubes"]
    flow_full = RESULTS["flow_96_tubes"]
    assert isinstance(flow17, dict) and isinstance(flow_full, dict)
    out: dict[str, object] = {}
    for label, experiment_id in (
        ("17 管", flow17["experiment_id"]),
        (f"{TUBE_COUNT} 管", flow_full["experiment_id"]),
    ):
        for language in (ReportLanguage.ZH_CN, ReportLanguage.EN):
            _, seconds = timed(
                lambda e=experiment_id, lang=language: reports.preview(analyst, str(e), lang)  # type: ignore[misc]
            )
            out[f"预览 {label} {language.value} (ms)"] = round(seconds * 1000, 1)
    for label, experiment_id in (
        ("17 管", flow17["experiment_id"]),
        (f"{TUBE_COUNT} 管", flow_full["experiment_id"]),
    ):
        preview = reports.preview(analyst, str(experiment_id), ReportLanguage.ZH_CN)
        writer = QPdfWriter(str(tmp / f"paint-{label[:2]}.pdf"))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        pages, seconds = timed(lambda p=preview, w=writer: reports.paint(p, w))
        out[f"渲染成 PDF {label}（{pages} 页）(ms)"] = round(seconds * 1000, 1)
    record = reports.signed_reports(analyst)[0]
    path = reports.resolve(record)
    out["已签署 PDF 大小 (KB)"] = round(path.stat().st_size / 1024, 1)
    _, seconds = timed(
        lambda: reports.export_report_file(analyst, record.report_id, tmp / "one.pdf")
    )
    out["导出单份报告 (ms)"] = round(seconds * 1000, 1)
    # 再签署一份英文报告：用 17 管试验的新一轮
    RESULTS["reports"] = out
    log(f"报告：{out}")


def measure_permissions(rig: support.Rig, tmp: Path) -> None:
    """全部角色乘全部权限，核对服务层和界面路由。"""
    context = rig.context
    sessions = {
        Role.ADMIN: context.auth.login("admin", support.DEMO_PASSWORDS["admin"]),
        Role.EXPERIMENTER: context.auth.login(
            "experimenter", support.DEMO_PASSWORDS["experimenter"]
        ),
        Role.ANALYST: context.auth.login("analyst", support.DEMO_PASSWORDS["analyst"]),
    }
    mismatches: list[str] = []
    checks = 0
    for role, session in sessions.items():
        for permission in Permission:
            checks += 1
            expected = has_permission(role, permission)
            try:
                context.auth.authorize(session, permission)
                allowed = True
            except PermissionDeniedError:
                allowed = False
            if allowed != expected:
                mismatches.append(f"authorize {role.value}/{permission.value}")
    calls: dict[Permission, Callable[[object], object]] = {
        Permission.USER_MANAGE: lambda s: context.users.list_users(s),  # type: ignore[arg-type]
        Permission.AUDIT_VIEW: lambda s: context.audit.query(
            s, AuditFilter(), PageRequest(limit=1)
        ),  # type: ignore[arg-type]
        Permission.TEMPLATE_MANAGE: lambda s: context.templates.create_template(
            s,  # type: ignore[arg-type]
            TemplateDraft("权限检查", ExperimentType.LIMIT, [TemplateItemInput("x")]),
        ),
        Permission.DATA_EXPORT: lambda s: context.exports.export_results(
            s,  # type: ignore[arg-type]
            ["不存在"],
            tmp / "perm.xlsx",
        ),
        Permission.RESULT_REVIEW: lambda s: context.reviews.review_tube(
            s,  # type: ignore[arg-type]
            "不存在",
            1,
            Interpretation.POSITIVE,
            "x",
        ),
        Permission.EXPERIMENT_SIGN: lambda s: context.reports.sign(s, "不存在", "x"),  # type: ignore[arg-type]
        Permission.REPORT_VIEW: lambda s: context.reports.preview(s, "不存在"),  # type: ignore[arg-type]
    }
    service_checks = 0
    for role, session in sessions.items():
        for permission, call in calls.items():
            service_checks += 1
            expected = has_permission(role, permission)
            try:
                call(session)
                denied = False
            except PermissionDeniedError:
                denied = True
            except Exception:  # noqa: BLE001 - 有权限时因为数据不存在而失败是预期的
                denied = False
            if denied == expected:
                mismatches.append(f"service {role.value}/{permission.value}")
    route_checks = 0
    for role, session in sessions.items():
        for route in PAGE_ROUTES:
            route_checks += 1
            allowed = can_enter(route, session)
            expected = route.permission is None or has_permission(role, route.permission)
            if allowed != expected:
                mismatches.append(f"route {role.value}/{route.key}")
    RESULTS["permissions"] = {
        "permissions": len(PERMISSION_ROLES),
        "authorize_checks": checks,
        "service_checks": service_checks,
        "route_checks": route_checks,
        "mismatches": mismatches,
    }
    log(f"权限：{RESULTS['permissions']}")


def measure_security(rig: support.Rig, tmp: Path) -> None:
    """加密防护抽查。"""
    context = rig.context
    out: dict[str, object] = {}
    data_dir = rig.window.services.data_dir
    keys = list((data_dir / "keys").glob("*"))
    key_file = data_dir / "keys" / "report-signing.key"
    head = key_file.read_bytes()[:10] if key_file.exists() else b""
    out["签名私钥文件头"] = head.decode("ascii", "replace")
    out["私钥不是明文 PEM"] = b"BEGIN" not in key_file.read_bytes() if key_file.exists() else False
    out["密钥目录文件"] = sorted(p.name for p in keys)
    with sqlite3.connect(str(data_dir / "gelinspect.db")) as connection:
        hashes = [r[0] for r in connection.execute("select password_hash from users")]
    out["密码哈希均为 argon2"] = all(h.startswith("$argon2") for h in hashes)
    admin = context.auth.login("admin", support.DEMO_PASSWORDS["admin"])
    analyst = context.auth.login("analyst", support.DEMO_PASSWORDS["analyst"])
    # 登录失败锁定
    locked = False
    for _ in range(context.auth.policy.max_attempts + 1):
        try:
            context.auth.login("experimenter", "错误的密码")
        except AccountLockedError:
            locked = True
            break
        except AuthError:
            continue
    out["连续输错密码后锁定"] = locked
    # 导出加密
    summary = context.exports.export_results(
        admin, ["E000"], tmp / "enc.xlsx", password="Export#Pass-2026"
    )
    path = summary.path
    raw = path.read_bytes()
    out["加密导出文件"] = path.name
    out["加密导出是 ZIP"] = zipfile.is_zipfile(path)
    out["密文中不含明文表格标记"] = b"xl/workbook" not in raw and b"sharedStrings" not in raw
    try:
        read_encrypted_zip(path, "wrong-password-123")
        out["错误密码不能解开"] = False
    except Exception:  # noqa: BLE001 - 任何失败都说明没有解开
        out["错误密码不能解开"] = True
    out["正确密码能解开"] = bool(read_encrypted_zip(path, "Export#Pass-2026"))
    # 签名 PDF 改一个字节
    record = context.reports.signed_reports(analyst)[0]
    pdf = context.reports.resolve(record)
    original = pdf.read_bytes()
    broken = bytearray(original)
    broken[len(broken) // 2] ^= 0x01
    pdf.write_bytes(bytes(broken))
    out["改一个字节后验签失败"] = not context.reports.verify_report(analyst, record.report_id).ok
    pdf.write_bytes(original)
    out["还原后验签通过"] = context.reports.verify_report(analyst, record.report_id).ok
    # 审计链：改一条记录的内容
    chain = context.audit.verify_chain(admin)
    out["审计链校验（改动前）"] = bool(chain.ok)
    with sqlite3.connect(str(data_dir / "gelinspect.db")) as connection:
        seq, detail = connection.execute(
            "select seq, detail from audit_events where seq = 50"
        ).fetchone()
        connection.execute("update audit_events set detail = ? where seq = 50", (detail + "x",))
    tampered = context.audit.verify_chain(admin)
    out["审计链：改一条记录后检出"] = (not tampered.ok) and tampered.first_bad_seq == seq
    out["审计链：定位到的序号"] = tampered.first_bad_seq
    with sqlite3.connect(str(data_dir / "gelinspect.db")) as connection:
        connection.execute("update audit_events set detail = ? where seq = 50", (detail,))
    out["审计链：还原后通过"] = bool(context.audit.verify_chain(admin).ok)
    RESULTS["security"] = out
    log(f"加密防护：{out}")


def measure_i18n() -> None:
    """翻译文件完整性和英文界面中文残留。"""
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "update_translations.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    scan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_english_ui.py")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    hits = [line for line in scan.stdout.splitlines() if line.startswith("  ")]
    audit_hits = [line for line in hits if ":audit /" in line]
    RESULTS["i18n"] = {
        "translation_check_returncode": check.returncode,
        "translation_check_output": check.stderr.strip()[:300],
        "english_scan_returncode": scan.returncode,
        "english_scan_hits_total": len(hits),
        "english_scan_hits_outside_audit": len(hits) - len(audit_hits),
        "english_scan_sample_outside_audit": [h.strip() for h in hits if h not in audit_hits][:8],
    }
    log(f"多语言：{RESULTS['i18n']}")


def measure_perf_tests() -> None:
    """运行已有的 perf 测试，收集它们打印的数字。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "perf", "-s", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    lines = [ln for ln in proc.stdout.splitlines() if "[perf]" in ln or "批量导出" in ln]
    RESULTS["perf_tests"] = {
        "returncode": proc.returncode,
        "summary": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "",
        "lines": lines,
    }
    log(f"perf 测试：返回码 {proc.returncode}，{RESULTS['perf_tests']['summary']}")  # type: ignore[index]


def main() -> int:
    """依次测量并输出。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="把结果写成 JSON")
    parser.add_argument("--skip-perf-tests", action="store_true", help="不运行 pytest -m perf")
    args = parser.parse_args()
    measure_environment()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rig = measure_flow(root / "run")
        try:
            seed_big_data(rig)
            measure_ui_response(rig)
            measure_queries_and_export(rig, root)
            measure_reports(rig, root)
            measure_permissions(rig, root)
            measure_security(rig, root)
        finally:
            support.close_rig(rig)
    measure_i18n()
    if not args.skip_perf_tests:
        measure_perf_tests()
    if args.json:
        args.json.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
