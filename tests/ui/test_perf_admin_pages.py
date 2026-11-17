"""性能（默认运行，可用 ``-m "not perf"`` 跳过）：10 万条审计 + 1 万管结果下，各页查询和翻页、
界面切页都应小于 1 秒，并报告 Excel 导出 1 万管的耗时。

实测数字用 ``pytest -s tests/ui/test_perf_admin_pages.py`` 查看。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext, build_app_context
from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.config import parse_config
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.audit_page import AuditPage
from gelinspect.ui.pages.history_page import HistoryPage
from gelinspect.ui.pages.settings_page import SettingsPage
from gelinspect.ui.pages.users_page import UsersPage
from gelinspect.ui.widgets.app_shell import MemoryShellSettings
from tests.infrastructure.db.conftest import T0, make_event, make_experiment
from tests.ui.conftest import DEMO_PASSWORDS, FIXED_NOW, make_config, settle, wait_for
from tests.ui.monitor_helpers import FAST_ENGINE, FAST_VIDEO, close_context

pytestmark = pytest.mark.perf

AUDIT_COUNT = 100_000
EXPERIMENT_COUNT = 125
TUBES_PER_EXPERIMENT = 80  # 125 × 80 = 10000 管
LIMIT_SECONDS = 1.0
ACTORS = [f"user{i:02d}" for i in range(20)]
ACTIONS = list(AuditAction)
REPORT: list[str] = []


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AppContext]:
    root: Path = tmp_path_factory.mktemp("perf-admin")
    config = make_config(root, video=FAST_VIDEO, engine=FAST_ENGINE)
    context = build_app_context(parse_config(config), clock=lambda: FIXED_NOW)
    started = time.perf_counter()
    for start in range(0, AUDIT_COUNT, 10_000):
        context.database.audit.append_many(
            [
                make_event(
                    i,
                    actor=ACTORS[i % len(ACTORS)],
                    action=ACTIONS[i % len(ACTIONS)],
                    detail=f"记录 {i} 批号 LAL-{i % 500:03d}",
                    experiment_id=f"E{i % EXPERIMENT_COUNT:03d}",
                )
                for i in range(start, start + 10_000)
            ]
        )
    audit_s = time.perf_counter() - started
    started = time.perf_counter()
    for n in range(EXPERIMENT_COUNT):
        experiment = make_experiment(
            f"E{n:03d}",
            experiment_type=list(ExperimentType)[n % len(ExperimentType)],
            created_at=T0 + timedelta(hours=n),
            tube_count=TUBES_PER_EXPERIMENT,
            batch_no=f"LAL-{n % 25:03d}",
        )
        experiment.operator = "experimenter" if n % 2 else "admin"
        experiment.state = [ExperimentState.SIGNED, ExperimentState.COMPLETED][n % 2]
        context.database.experiments.save(experiment)
    tubes_s = time.perf_counter() - started
    REPORT.append(
        f"写入 {AUDIT_COUNT} 条审计 {audit_s:.2f}s；"
        f"{EXPERIMENT_COUNT * TUBES_PER_EXPERIMENT} 管结果 {tubes_s:.2f}s"
    )
    yield context
    close_context(context)
    print("\n[perf] " + "\n[perf] ".join(REPORT))


@pytest.fixture
def big_window(qapp: QApplication, big: AppContext) -> Iterator[MainWindow]:
    window = MainWindow(
        big.services(),
        LanguageManager(qapp),
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    window.resize(1280, 800)
    window.show()
    settle(qapp)
    window.login_page.username.setText("admin")
    window.login_page.password.setText(DEMO_PASSWORDS["admin"])
    window.login_page.submit_button.click()
    settle(qapp)
    yield window
    page = window.page("audit")
    if isinstance(page, AuditPage):
        page.wait_idle()
    window.bridge.close()
    window.close()
    window.deleteLater()
    settle(qapp)


def timed(label: str, call: Callable[[], object], *, limit: float = LIMIT_SECONDS) -> float:
    started = time.perf_counter()
    call()
    elapsed = time.perf_counter() - started
    REPORT.append(f"{label}: {elapsed * 1000:.1f} ms")
    assert elapsed < limit, f"{label} 用时 {elapsed:.3f}s，超过 {limit}s"
    return elapsed


def test_dataset_sizes(big: AppContext) -> None:
    assert (
        big.database.audit.query(AuditFilter().to_query(), PageRequest(limit=1)).total
        >= AUDIT_COUNT
    )
    assert (
        big.database.experiments.query(ExperimentQuery(), PageRequest(limit=1)).total
        == EXPERIMENT_COUNT
    )


def test_service_queries_and_paging_stay_under_a_second(big: AppContext) -> None:
    admin = big.auth.login("admin", DEMO_PASSWORDS["admin"])
    audit, queries = big.audit, big.queries
    timed("审计页 首页", lambda: audit.query(admin, AuditFilter(), PageRequest(limit=50)))
    timed(
        "审计页 翻页 offset=50000",
        lambda: audit.query(
            admin, AuditFilter(max_seq=100_000), PageRequest(offset=50_000, limit=50)
        ),
    )
    timed(
        "审计页 翻页 offset=99950",
        lambda: audit.query(
            admin, AuditFilter(max_seq=100_000), PageRequest(offset=99_950, limit=50)
        ),
    )
    timed(
        "审计页 按操作者",
        lambda: audit.query(admin, AuditFilter(actor="user07"), PageRequest(limit=50)),
    )
    timed(
        "审计页 按时间段",
        lambda: audit.query(
            admin,
            AuditFilter(start=T0 + timedelta(hours=5), end=T0 + timedelta(hours=6)),
            PageRequest(limit=50),
        ),
    )
    timed(
        "审计页 按关键字（全表子串）",
        lambda: audit.query(admin, AuditFilter(keyword="LAL-123"), PageRequest(limit=50)),
    )
    timed(
        "审计页 组合条件",
        lambda: audit.query(
            admin,
            AuditFilter(actor="user03", keyword="批号", start=T0, end=T0 + timedelta(days=30)),
            PageRequest(limit=50),
        ),
    )
    timed("审计页 操作者下拉", lambda: audit.actors(admin))
    timed(
        "历史页 首页",
        lambda: queries.search_experiments(admin, ExperimentQuery(), PageRequest(limit=20)),
    )
    timed(
        "历史页 翻页 offset=100",
        lambda: queries.search_experiments(
            admin, ExperimentQuery(), PageRequest(offset=100, limit=20)
        ),
    )
    timed(
        "历史页 按类型",
        lambda: queries.search_experiments(
            admin, ExperimentQuery(experiment_type=ExperimentType.LIMIT), PageRequest(limit=20)
        ),
    )
    timed(
        "历史页 按批号",
        lambda: queries.search_experiments(
            admin, ExperimentQuery(batch_no="LAL-007"), PageRequest(limit=20)
        ),
    )
    timed(
        "历史页 按状态+时间+操作人",
        lambda: queries.search_experiments(
            admin,
            ExperimentQuery(
                state=ExperimentState.SIGNED,
                start=T0,
                end=T0 + timedelta(hours=60),
                operator="admin",
            ),
            PageRequest(limit=20),
        ),
    )
    timed("用户页 列表", lambda: big.users.list_users(admin))
    timed("设置页 读取", lambda: big.settings.current(admin))


def test_verifying_100k_audit_records(big: AppContext) -> None:
    admin = big.auth.login("admin", DEMO_PASSWORDS["admin"])
    seen: list[int] = []
    elapsed = timed(
        "校验 10 万条哈希链",
        lambda: big.audit.verify_chain(admin, lambda done, _t: seen.append(done)),
        limit=5.0,
    )
    assert seen and seen[-1] >= AUDIT_COUNT and elapsed < 5.0


def test_switching_pages_is_under_a_second_and_data_appears_in_time(
    qapp: QApplication, big_window: MainWindow
) -> None:
    window = big_window
    # 首次进入（创建页面 + 首次查询）
    for key in ("history", "audit", "users", "settings", "dashboard"):
        elapsed = timed(f"切页 {key}（首次，返回）", lambda key=key: window.navigate(key))  # type: ignore[misc]
        assert elapsed < LIMIT_SECONDS
    history = window.page("history")
    audit = window.page("audit")
    users = window.page("users")
    settings = window.page("settings")
    assert isinstance(history, HistoryPage) and isinstance(audit, AuditPage)
    assert isinstance(users, UsersPage) and isinstance(settings, SettingsPage)
    # 再次切回：数据在 1 秒内显示出来（审计和设置的数据在后台线程里读）
    for key, page, ready in (
        ("history", history, lambda: history.total == EXPERIMENT_COUNT),
        ("audit", audit, lambda: not audit.loading and audit.total >= AUDIT_COUNT),
        ("users", users, lambda: bool(users.records())),
        ("settings", settings, lambda: not settings.previewing),
    ):
        started = time.perf_counter()
        window.navigate(key)
        assert wait_for(qapp, ready, LIMIT_SECONDS), f"{key} 的数据 1 秒内没有显示"
        elapsed = time.perf_counter() - started
        REPORT.append(f"切页 {key}（再次，数据显示）: {elapsed * 1000:.1f} ms")
        assert elapsed < LIMIT_SECONDS
        assert page is window.page(key)
    audit.wait_idle()


def test_excel_export_of_ten_thousand_tubes(big: AppContext, tmp_path: Path) -> None:
    admin = big.auth.login("admin", DEMO_PASSWORDS["admin"])
    ids = [f"E{n:03d}" for n in range(EXPERIMENT_COUNT)]
    started = time.perf_counter()
    summary = big.exports.export_results(admin, ids, tmp_path / "results.xlsx")
    elapsed = time.perf_counter() - started
    REPORT.append(
        f"Excel 导出 {summary.rows} 管结果（{EXPERIMENT_COUNT} 个试验，"
        f"{summary.path.stat().st_size / 1e6:.1f} MB）: {elapsed:.2f} s"
    )
    assert summary.rows == EXPERIMENT_COUNT * TUBES_PER_EXPERIMENT
    assert elapsed < 10.0  # 计划的批量导出指标
    started = time.perf_counter()
    audit_summary = big.exports.export_audit(
        admin, AuditFilter(start=T0, end=T0 + timedelta(days=2)), tmp_path / "audit.xlsx"
    )
    REPORT.append(f"Excel 导出审计 {audit_summary.rows} 条: {time.perf_counter() - started:.2f} s")
    started = time.perf_counter()
    history_summary = big.exports.export_history(
        admin, ExperimentQuery(), tmp_path / "history.xlsx"
    )
    REPORT.append(
        f"Excel 导出历史列表 {history_summary.rows} 行: {time.perf_counter() - started:.2f} s"
    )
