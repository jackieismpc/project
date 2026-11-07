"""服务层测试共用的夹具。"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest


def _worker_threads() -> list[str]:
    prefixes = ("experiment-engine", "engine-", "video-")
    return [t.name for t in threading.enumerate() if t.name.startswith(prefixes)]


@pytest.fixture
def thread_baseline() -> Iterator[None]:
    """断言测试结束后线程数回到基线，引擎和视频的线程都已退出。"""
    before = threading.active_count()
    yield
    deadline = time.monotonic() + 8.0
    while (threading.active_count() > before or _worker_threads()) and (
        time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert _worker_threads() == []
    assert threading.active_count() <= before


# ---------------------------------------------------------------------------
# P8：模板、试剂、新试验服务的共用夹具（真实 SQLite，试剂码和备案清单用模拟白名单）

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from gelinspect.app_context import AppContext, build_app_context  # noqa: E402
from gelinspect.bootstrap import Argon2Hasher, DatabaseFirstAdminSeeder, initialize_accounts  # noqa: E402
from gelinspect.domain.entities import User  # noqa: E402
from gelinspect.domain.enums import AuditAction, ReagentKind  # noqa: E402
from gelinspect.infrastructure.db import Database, open_database  # noqa: E402
from gelinspect.interfaces.storage import AuditQuery, PageRequest  # noqa: E402
from gelinspect.services.auth import AuthService, Session  # noqa: E402
from gelinspect.services.config import DEFAULT_CONFIG, parse_config  # noqa: E402
from gelinspect.services.experiment_service import ExperimentService  # noqa: E402
from gelinspect.services.reagent_service import ReagentService  # noqa: E402
from gelinspect.services.template_service import TemplateService  # noqa: E402
from simulation.demo_accounts import DEMO_ACCOUNTS, DemoAccounts  # noqa: E402
from simulation.reagents import SAMPLE_CODES, SimReagentCodeParser, SimReagentRegistry  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
"""测试里的“当前时间”：模拟白名单里的 LAL2606A 在此刻有效，LAL2501B 已过期。"""

GOOD_CODES: dict[ReagentKind, str] = {
    ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_valid"],
    ReagentKind.ENDOTOXIN: SAMPLE_CODES["endotoxin_valid"],
    ReagentKind.WATER: SAMPLE_CODES["water_valid"],
}
PASSWORDS = {a.username: a.password for a in DEMO_ACCOUNTS}


class StubEngine:
    """只记录启动调用的引擎替身。"""

    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None

    def start(self, experiment_id: str, user: User) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.started.append((experiment_id, user.username))


@dataclass
class Stack:
    """一套装配好的服务（不含真实引擎）。"""

    database: Database
    auth: AuthService
    reagents: ReagentService
    templates: TemplateService
    experiments: ExperimentService
    engine: StubEngine
    sessions: dict[str, Session] = field(default_factory=dict)

    def login(self, username: str) -> Session:
        """用演示账号登录（同一个账号复用会话）。"""
        if username not in self.sessions:
            self.sessions[username] = self.auth.login(username, PASSWORDS[username])
        return self.sessions[username]

    def audit(self, action: AuditAction | None = None) -> list[str]:
        """全部审计的操作记录文本，按时间正序。"""
        page = self.database.audit.query(AuditQuery(action=action), PageRequest(limit=1000))
        return [r.event.detail for r in sorted(page.items, key=lambda r: r.seq)]


def build_stack(tmp_path: Path, *, registry: SimReagentRegistry | None = None) -> Stack:
    database = open_database(tmp_path / "stack.db")
    initialize_accounts(database, DemoAccounts())
    auth = AuthService(
        database, Argon2Hasher(), clock=lambda: NOW, seeder=DatabaseFirstAdminSeeder(database)
    )
    reagents = ReagentService(
        auth,
        database,
        parser=SimReagentCodeParser(),
        registry=registry or SimReagentRegistry(),
        clock=lambda: NOW,
    )
    engine = StubEngine()
    return Stack(
        database=database,
        auth=auth,
        reagents=reagents,
        templates=TemplateService(auth, database),
        experiments=ExperimentService(auth, database, reagents, engine, clock=lambda: NOW),
        engine=engine,
    )


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    """真实 SQLite + 模拟白名单 + 引擎替身。"""
    built = build_stack(tmp_path)
    yield built
    built.database.close()


@pytest.fixture
def real_app(tmp_path: Path) -> Iterator[AppContext]:
    """完整装配（真实 SQLite、模拟设备、视频和算法），退出时有序关闭并确认没有线程残留。"""
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    config["data_dir"] = str(tmp_path / "data")
    context = build_app_context(parse_config(config), clock=lambda: NOW)
    yield context
    context.close()
    deadline = time.monotonic() + 8.0
    while _worker_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _worker_threads() == []
