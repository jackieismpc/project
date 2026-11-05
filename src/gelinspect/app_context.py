"""组装根：按配置创建全部后端，装配成试验引擎的依赖。

只有这里同时知道 ``services`` 和 ``infrastructure``，其余代码都依赖接口。
放在包的根部而不是 ``services/`` 里，是因为服务层不允许导入 ``infrastructure``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gelinspect.bootstrap import (
    Argon2Hasher,
    DatabaseFirstAdminSeeder,
    initialize_accounts,
    load_bootstrap_hook,
)
from gelinspect.infrastructure.db import Database, open_database
from gelinspect.infrastructure.video.factory import VideoSourceConfigError, create_video_source
from gelinspect.interfaces.analysis import GelAnalyzer
from gelinspect.interfaces.bootstrap import DemoAccount
from gelinspect.interfaces.device import DeviceController
from gelinspect.interfaces.traceability import (
    GovernmentTraceabilityClient,
    ReagentCodeParser,
    ReagentRegistry,
)
from gelinspect.interfaces.video import VideoSource
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthService, LoginPolicy
from gelinspect.services.config import (
    AppConfig,
    ConfigError,
    explain_import_failure,
    load_backend,
    load_config,
)
from gelinspect.services.engine_types import EngineEventListener, RecoveredExperiment
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.experiment_service import ExperimentService
from gelinspect.services.instrument_monitor import InstrumentMonitor
from gelinspect.services.queries import QueryService
from gelinspect.services.reagent_service import ReagentService
from gelinspect.services.template_service import TemplateService
from gelinspect.services.video import VideoPipeline


@dataclass(slots=True)
class AppContext:
    """装配好的应用依赖。

    Attributes:
        config: 配置。
        database: 数据库。
        device: 设备。
        video_source: 视频源。
        pipeline: 视频管线。
        analyzer: 算法。
        engine: 试验引擎。
        recovered: 启动恢复时被标记为异常的试验。
        auth: 登录与权限服务。
        queries: 只读查询服务。
        instrument: 仪器状态监视器，由界面决定何时启动。
        templates: 检测模板管理服务。
        reagents: 试剂扫码校验与溯源服务。
        experiments: 创建和启动试验的服务。
        demo_accounts: 初始化钩子提供的演示账号，没有钩子时为空。
        first_run: 本次是否为首次启动（用户表原本为空）。
    """

    config: AppConfig
    database: Database
    device: DeviceController
    video_source: VideoSource
    pipeline: VideoPipeline
    analyzer: GelAnalyzer
    engine: ExperimentEngine
    recovered: list[RecoveredExperiment]
    auth: AuthService
    queries: QueryService
    instrument: InstrumentMonitor
    templates: TemplateService
    reagents: ReagentService
    experiments: ExperimentService
    demo_accounts: tuple[DemoAccount, ...] = ()
    first_run: bool = False

    def services(self, *, log_dir: Path | None = None) -> AppServices:
        """生成界面使用的服务集合。

        Args:
            log_dir: 日志目录，默认数据目录下的 ``logs``。

        Returns:
            服务集合。
        """
        config = self.config
        return AppServices(
            auth=self.auth,
            queries=self.queries,
            engine=self.engine,
            instrument=self.instrument,
            templates=self.templates,
            reagents=self.reagents,
            experiments=self.experiments,
            demo_accounts=self.demo_accounts,
            idle_lock_minutes=config.security.idle_lock_minutes,
            recovered=tuple(self.recovered),
            data_dir=config.data_dir,
            log_dir=log_dir or config.data_dir / "logs",
            backends={
                "device": config.device.backend,
                "video": config.video.source,
                "analysis": config.analysis.backend,
            },
        )

    def close(self) -> None:
        """有序释放全部资源，可重复调用。

        顺序：仪器监视线程、试验引擎（还在进行的试验转异常）、视频管线、设备、数据库。
        """
        self.instrument.stop(5.0)
        self.engine.shutdown(30.0)
        self.pipeline.stop()
        self.device.close()
        self.database.close()


def _create_video_source(config: AppConfig) -> VideoSource:
    spec = config.video.source
    try:
        return create_video_source(spec, config.video.options)
    except VideoSourceConfigError as exc:
        cause = exc.__cause__ if isinstance(exc.__cause__, ImportError) else exc
        raise ConfigError(explain_import_failure("video.source", spec, cause)) from exc


def _load_traceability(
    config: AppConfig,
) -> tuple[ReagentCodeParser | None, ReagentRegistry | None, GovernmentTraceabilityClient | None]:
    """按配置加载试剂码解析器、备案清单和政府溯源客户端；没配置的为 None。"""
    section = config.traceability
    parser = (
        None
        if section.parser is None
        else load_backend(
            "traceability.parser",
            section.parser.backend,
            ReagentCodeParser,  # type: ignore[type-abstract]
            section.parser.options,
        )
    )
    registry = (
        None
        if section.registry is None
        else load_backend(
            "traceability.registry",
            section.registry.backend,
            ReagentRegistry,  # type: ignore[type-abstract]
            section.registry.options,
        )
    )
    government = (
        None
        if section.government is None
        else load_backend(
            "traceability.government",
            section.government.backend,
            GovernmentTraceabilityClient,  # type: ignore[type-abstract]
            section.government.options,
        )
    )
    return parser, registry, government


def build_app_context(
    config: AppConfig | None = None,
    *,
    config_path: Path | None = None,
    on_event: EngineEventListener | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AppContext:
    """按配置创建全部依赖，并执行启动恢复。

    先创建后端再打开数据库，这样配置有误时不会留下任何文件。

    Args:
        config: 配置，为 None 时从 ``config_path`` 读取。
        config_path: YAML 文件，为 None 时使用默认配置（指向 ``simulation/``）。
        on_event: 引擎事件监听者。
        clock: 试剂校验和试验创建使用的当前时间（带时区），默认系统时间；测试注入固定日期，
            这样模拟白名单的有效期不会随真实日期过期。

    Returns:
        应用依赖。

    Raises:
        ConfigError: 配置有误，或后端无法加载。
        DatabaseVersionError: 数据库版本比程序新。
    """
    settings = config if config is not None else load_config(config_path)
    device = load_backend(
        "device.backend",
        settings.device.backend,
        DeviceController,  # type: ignore[type-abstract]
        settings.device.options,
    )
    source = _create_video_source(settings)
    analyzer = load_backend(
        "analysis.backend",
        settings.analysis.backend,
        GelAnalyzer,  # type: ignore[type-abstract]
        settings.analysis.options,
    )
    hook = load_bootstrap_hook(settings)
    parser, registry, government = _load_traceability(settings)
    pipeline = VideoPipeline(source, settings.pipeline_config())
    database = open_database(settings.database_path)
    try:
        first_run = initialize_accounts(database, hook)
        security = settings.security
        auth = AuthService(
            database,
            Argon2Hasher(),
            policy=LoginPolicy(
                max_attempts=security.max_login_attempts,
                lockout_s=security.lockout_minutes * 60.0,
            ),
            seeder=DatabaseFirstAdminSeeder(database),
        )
        engine = ExperimentEngine(
            device,
            pipeline,
            analyzer,
            database,
            config=settings.engine_config(),
            mapping=settings.mapping(),
            analyzer_config_path=settings.analysis.config_path,
            on_event=on_event,
        )
        recovered = engine.recover_interrupted()
        reagents = ReagentService(
            auth,
            database,
            parser=parser,
            registry=registry,
            government=government,
            clock=clock,
        )
    except BaseException:
        database.close()
        raise
    return AppContext(
        config=settings,
        database=database,
        device=device,
        video_source=source,
        pipeline=pipeline,
        analyzer=analyzer,
        engine=engine,
        recovered=recovered,
        auth=auth,
        queries=QueryService(auth, database),
        instrument=InstrumentMonitor(engine),
        templates=TemplateService(auth, database),
        reagents=reagents,
        experiments=ExperimentService(
            auth, database, reagents, engine, instrument_no=settings.instrument_no, clock=clock
        ),
        demo_accounts=tuple(hook.accounts()) if hook is not None else (),
        first_run=first_run,
    )
