"""组装根：按配置创建全部后端，装配成试验引擎的依赖。

只有这里同时知道 ``services`` 和 ``infrastructure``，其余代码都依赖接口。
放在包的根部而不是 ``services/`` 里，是因为服务层不允许导入 ``infrastructure``。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
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
from gelinspect.services.audit_service import AuditService
from gelinspect.services.auth import AuthService, LoginPolicy
from gelinspect.services.config import (
    AppConfig,
    ConfigError,
    explain_import_failure,
    load_backend,
    load_config,
)
from gelinspect.services.dashboard_service import DashboardService
from gelinspect.services.engine_types import EngineEventListener, RecoveredExperiment
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.experiment_service import ExperimentService
from gelinspect.services.export_service import ExportService
from gelinspect.services.instrument_monitor import InstrumentMonitor
from gelinspect.services.media_service import MediaService
from gelinspect.services.monitor_service import MonitorService
from gelinspect.services.queries import QueryService
from gelinspect.services.reagent_service import ReagentService
from gelinspect.services.reporting.keystore import LocalKeyStore
from gelinspect.services.reporting.report_service import ReportService
from gelinspect.services.review_service import ReviewService
from gelinspect.services.settings_service import InstrumentSettingsService
from gelinspect.services.template_service import TemplateService
from gelinspect.services.user_service import UserService
from gelinspect.services.user_settings import apply_overrides, load_overrides, settings_file
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
        monitor: 试验监控服务。
        media: 原图、掩码和录像服务。
        reviews: 人工复核服务。
        dashboard: 工作台数据服务。
        reports: 报告服务。
        users: 用户与权限管理服务。
        audit: 审计追踪服务。
        exports: Excel 导出服务。
        settings: 仪器设置服务。
        base_config: 没有叠加用户设置的默认配置。
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
    monitor: MonitorService
    media: MediaService
    reviews: ReviewService
    dashboard: DashboardService
    reports: ReportService
    users: UserService
    audit: AuditService
    exports: ExportService
    settings: InstrumentSettingsService
    base_config: AppConfig
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
            monitor=self.monitor,
            media=self.media,
            reviews=self.reviews,
            dashboard=self.dashboard,
            reports=self.reports,
            users=self.users,
            audit=self.audit,
            exports=self.exports,
            settings=self.settings,
            demo_accounts=self.demo_accounts,
            idle_lock_minutes=config.security.idle_lock_minutes,
            recovered=tuple(self.recovered),
            data_dir=config.data_dir,
            log_dir=log_dir or config.data_dir / "logs",
            backends=_LiveBackends(self),
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


class _LiveBackends(Mapping[str, str]):
    """关于页显示的后端：随仪器设置保存而更新（视频源可以在运行中更换）。"""

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def _view(self) -> dict[str, str]:
        config = self._context.config
        return {
            "device": config.device.backend,
            "video": config.video.source,
            "analysis": config.analysis.backend,
        }

    def __getitem__(self, key: str) -> str:
        return self._view()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._view())

    def __len__(self) -> int:
        return len(self._view())


class _RuntimeApplier:
    """把保存后的仪器设置应用到运行中的引擎、视频管线、试验服务和报告服务。

    组装根的一部分：只有这里同时知道视频源的具体创建方式和各服务。必须在没有试验运行时调用，
    引擎和管线会拒绝运行中的更换；任何一步失败都撤销已做的，不留下半个状态。
    """

    def __init__(
        self,
        engine: ExperimentEngine,
        pipeline: VideoPipeline,
        experiments: ExperimentService,
        reports: ReportService,
        config: AppConfig,
    ) -> None:
        self._engine = engine
        self._pipeline = pipeline
        self._experiments = experiments
        self._reports = reports
        self._config = config
        self._context: AppContext | None = None

    def attach(self, context: AppContext) -> None:
        """记下上下文，之后把生效的配置和视频源同步给它。"""
        self._context = context

    def apply(self, new: AppConfig) -> None:
        """应用新配置。

        Args:
            new: 新配置。

        Raises:
            ConfigError: 视频源创建失败。
            EngineBusyError: 有试验在运行。
            PipelineStateError: 视频管线在运行。
        """
        old = self._config
        source = _create_video_source(new)
        self._engine.update_config(new.engine_config())
        try:
            self._pipeline.reconfigure(source, new.pipeline_config())
        except BaseException:
            self._engine.update_config(old.engine_config())
            raise
        self._experiments.set_instrument_no(new.instrument_no)
        self._reports.set_copyright_holder(new.reports.copyright_holder)
        self._config = new
        if self._context is not None:
            self._context.config = new
            self._context.video_source = source


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
            这样模拟白名单的有效期不会随真实日期过期。它只用于服务（试剂校验、试验创建和签署、
            审计、报告）：试验引擎、设备和视频始终用真实的系统时钟（它们的时间要和设备、画面帧同一
            基准）。所以注入固定时钟再跑真实试验，报告上「检测日期」（引擎写的开始事件）会晚于
            「签署日期」（服务时钟）；不注入则时间轴一致，见 test_real_clock_flow.py。

    Returns:
        应用依赖。

    Raises:
        ConfigError: 配置有误，或后端无法加载。
        DatabaseVersionError: 数据库版本比程序新。
    """
    base_config = config if config is not None else load_config(config_path)
    # 用户在「仪器设置」里改过的项保存在数据目录里，叠加到默认配置上；文件有错在这里报错，
    # 此时还没有创建任何后端和文件。
    settings = apply_overrides(base_config, load_overrides(settings_file(base_config.data_dir)))
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
    monitor = MonitorService(auth, database, engine, pipeline)
    media = MediaService(auth, database)
    reports = settings.reports
    report_service = ReportService(
        auth,
        database,
        data_dir=settings.data_dir,
        keystore=LocalKeyStore(
            settings.keys_dir,
            common_name=reports.certificate_common_name,
            organization=reports.certificate_organization,
            valid_days=reports.certificate_valid_days,
            algorithm=reports.key_algorithm,
        ),
        media=media,
        copyright_holder=reports.copyright_holder,
        include_images=reports.include_images,
        clock=clock,
    )
    experiments = ExperimentService(
        auth, database, reagents, engine, instrument_no=settings.instrument_no, clock=clock
    )
    applier = _RuntimeApplier(engine, pipeline, experiments, report_service, settings)
    context = AppContext(
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
        experiments=experiments,
        monitor=monitor,
        media=media,
        reviews=ReviewService(auth, database, clock=clock),
        dashboard=DashboardService(auth, database, monitor, reagents),
        reports=report_service,
        users=UserService(auth, database, clock=clock),
        audit=AuditService(auth, database, clock=clock),
        exports=ExportService(auth, database, clock=clock),
        settings=InstrumentSettingsService(
            auth,
            database,
            engine,
            base_config=base_config,
            effective_config=settings,
            apply=applier.apply,
            source_factory=create_video_source,
            clock=clock,
        ),
        base_config=base_config,
        demo_accounts=tuple(hook.accounts()) if hook is not None else (),
        first_run=first_run,
    )
    applier.attach(context)
    return context
