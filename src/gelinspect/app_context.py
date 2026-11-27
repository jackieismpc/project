"""组装根：按配置创建全部后端，装配成试验引擎的依赖。

只有这里同时知道 ``services`` 和 ``infrastructure``，其余代码都依赖接口。
放在包的根部而不是 ``services/`` 里，是因为服务层不允许导入 ``infrastructure``。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from gelinspect.bootstrap import (
    Argon2Hasher,
    DatabaseFirstAdminSeeder,
    initialize_accounts,
    load_bootstrap_hook,
)
from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db import Database, open_database
from gelinspect.infrastructure.db.seed import SYSTEM_ACTOR
from gelinspect.infrastructure.security.audit_keys import (
    AUDIT_KEY_FILE,
    ActivationResult,
    AuditKeyRing,
    activate_audit_hmac,
)
from gelinspect.infrastructure.security.protectors import (
    MultiKeyProtector,
    ProtectionMode,
    create_key_protector,
)
from gelinspect.infrastructure.video.factory import VideoSourceConfigError, create_video_source
from gelinspect.interfaces.analysis import GelAnalyzer
from gelinspect.interfaces.bootstrap import DemoAccount
from gelinspect.interfaces.device import DeviceController
from gelinspect.interfaces.security import KeyProtectionError
from gelinspect.interfaces.traceability import (
    GovernmentTraceabilityClient,
    ReagentCodeParser,
    ReagentRegistry,
)
from gelinspect.interfaces.video import VideoSource
from gelinspect.services.app_services import AppServices
from gelinspect.services.appearance_service import AppearanceService
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
from gelinspect.services.diagnostics_service import DiagnosticsService
from gelinspect.services.engine_types import EngineEventListener, RecoveredExperiment
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.experiment_service import ExperimentService
from gelinspect.services.export_service import ExportPolicy, ExportService
from gelinspect.services.instrument_monitor import InstrumentMonitor
from gelinspect.services.license import LICENSE_FILE, LicenseService
from gelinspect.services.media_service import MediaService
from gelinspect.services.monitor_service import MonitorService
from gelinspect.services.queries import QueryService
from gelinspect.services.reagent_service import ReagentService
from gelinspect.services.reporting.keystore import KeyStoreError, LocalKeyStore
from gelinspect.services.reporting.report_service import ReportService
from gelinspect.services.review_service import ReviewService
from gelinspect.services.settings_service import InstrumentSettingsService, VideoProbe, _shrink
from gelinspect.services.template_service import TemplateService
from gelinspect.services.user_service import UserService
from gelinspect.services.user_settings import apply_overrides, load_overrides, settings_file
from gelinspect.services.video import VideoPipeline

_log = logging.getLogger(__name__)

KEY_PROTECTION_ENV = "GELINSPECT_KEY_PROTECTION"
"""覆盖 ``security.key_protection`` 的环境变量（``auto``、``os``、``file``）。"""


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
        demo_accounts: 初始化钩子提供的账号快捷填入信息，没有钩子时为空。
        first_run: 本次是否为首次启动（用户表原本为空）。
        key_protector: 签名私钥和审计链密钥的保护方式。
        audit_keys: 启动时启用审计链 HMAC 的结果。
        license: 设备绑定授权（默认不启用）。
        export_policy: 导出加密策略。
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
    key_protector: MultiKeyProtector | None = None
    audit_keys: ActivationResult | None = None
    license: LicenseService | None = None
    export_policy: ExportPolicy | None = None

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
            diagnostics=DiagnosticsService(
                self.auth,
                self.database,
                data_dir=config.data_dir,
                log_dir=log_dir or config.data_dir / "logs",
                retention_days=lambda: self.settings.retention_days,
                video_source=config.video.source,
            ),
            appearance=AppearanceService(config.data_dir),
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
        first_error: Exception | None = None

        def failed(name: str, error: Exception) -> None:
            nonlocal first_error
            _log.error("关闭%s失败：%s", name, error, exc_info=error)
            if first_error is None:
                first_error = error

        try:
            monitor_stopped = self.instrument.stop(5.0)
            if not monitor_stopped:
                failed("仪器监视线程", TimeoutError("等待仪器监视线程退出超时"))
        except Exception as exc:
            monitor_stopped = False
            failed("仪器监视线程", exc)

        try:
            engine_stopped = self.engine.shutdown(30.0)
            if not engine_stopped:
                failed("试验引擎", TimeoutError("等待试验引擎及事件线程退出超时"))
        except Exception as exc:
            engine_stopped = False
            failed("试验引擎", exc)

        # 引擎超时后可能仍在使用视频管线、设备和数据库；保留这些资源，避免和工作线程并发销毁。
        if not engine_stopped:
            if first_error is not None:
                raise first_error
            return

        try:
            self.pipeline.stop()
        except Exception as exc:
            failed("视频管线", exc)

        # 仪器监视器直接调用设备控制器；探测超时后可能仍在驱动中，因此不在其下方关闭控制器。
        if monitor_stopped:
            try:
                self.device.close()
            except Exception as exc:
                failed("设备", exc)

        try:
            self.database.close()
        except Exception as exc:
            failed("数据库", exc)

        if first_error is not None:
            raise first_error


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
        export_policy: ExportPolicy,
    ) -> None:
        self._engine = engine
        self._pipeline = pipeline
        self._experiments = experiments
        self._reports = reports
        self._config = config
        self._export_policy = export_policy
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
        self._export_policy.set_encrypt(new.exports.encrypt)
        self._config = new
        if self._context is not None:
            self._context.config = new
            self._context.video_source = source


def _probe_running_pipeline(pipeline: VideoPipeline) -> VideoProbe | None:
    if not pipeline.is_running:
        return None
    info = pipeline.info
    if info is None:
        return None
    latest = pipeline.buffer.latest()
    if latest is None:
        deadline = time.monotonic() + 0.5
        while pipeline.is_running and time.monotonic() < deadline:
            time.sleep(0.02)
            latest = pipeline.buffer.latest()
            if latest is not None:
                break
    return VideoProbe(
        width=info.width,
        height=info.height,
        fps=info.fps,
        frame_count=info.frame_count,
        description=info.description,
        frame=None if latest is None else _shrink(latest.image),
    )


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
            基准）。所以注入固定时钟再跑真实试验，报告上“检测日期”（引擎写的开始事件）会晚于
            “签署日期”（服务时钟）；不注入则时间轴一致，见 test_real_clock_flow.py。

    Returns:
        应用依赖。

    Raises:
        ConfigError: 配置有误，或后端无法加载。
        DatabaseVersionError: 数据库版本比程序新。
    """
    with ExitStack() as resources:
        context = _assemble_app_context(
            config,
            config_path=config_path,
            on_event=on_event,
            clock=clock,
            resources=resources,
        )
        # 构建成功后由 AppContext 接管资源；后续启动失败时由其 close() 负责释放。
        resources.pop_all()
        return context


def _assemble_app_context(
    config: AppConfig | None,
    *,
    config_path: Path | None,
    on_event: EngineEventListener | None,
    clock: Callable[[], datetime] | None,
    resources: ExitStack,
) -> AppContext:
    """构建上下文，并在取得每个外部资源时登记清理操作。"""
    base_config = config if config is not None else load_config(config_path)
    # 用户在“仪器设置”里改过的项保存在数据目录里，叠加到默认配置上；文件有错在这里报错，
    # 此时还没有创建任何后端和文件。
    settings = apply_overrides(base_config, load_overrides(settings_file(base_config.data_dir)))
    engine_stopped = [True]
    device = load_backend(
        "device.backend",
        settings.device.backend,
        DeviceController,  # type: ignore[type-abstract]
        settings.device.options,
    )
    resources.callback(_cleanup_startup_resource, "设备", device.close, engine_stopped)
    source = _create_video_source(settings)
    resources.callback(_cleanup_startup_resource, "视频源", source.close, engine_stopped)
    analyzer = load_backend(
        "analysis.backend",
        settings.analysis.backend,
        GelAnalyzer,  # type: ignore[type-abstract]
        settings.analysis.options,
    )
    hook = load_bootstrap_hook(settings)
    parser, registry, government = _load_traceability(settings)
    pipeline = VideoPipeline(source, settings.pipeline_config())
    resources.callback(_cleanup_startup_resource, "视频管线", pipeline.stop, engine_stopped)
    try:
        protector = create_key_protector(settings.keys_dir, key_protection_mode(settings))
    except KeyProtectionError as exc:
        raise ConfigError(f"security.key_protection：{exc}") from exc
    database = open_database(settings.database_path)
    resources.callback(_cleanup_startup_resource, "数据库", database.close, engine_stopped)
    audit_keys = activate_audit_hmac(
        database,
        AuditKeyRing(settings.keys_dir / AUDIT_KEY_FILE, protector),
        clock=clock,
    )
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
    resources.callback(_cleanup_engine_startup, engine, 30.0, engine_stopped)
    recovered = engine.recover_interrupted()
    reagents = ReagentService(
        auth,
        database,
        parser=parser,
        registry=registry,
        government=government,
        clock=clock,
    )
    monitor = MonitorService(auth, database, engine, pipeline)
    media = MediaService(auth, database)
    reports = settings.reports
    keystore = LocalKeyStore(
        settings.keys_dir,
        common_name=reports.certificate_common_name,
        organization=reports.certificate_organization,
        valid_days=reports.certificate_valid_days,
        algorithm=reports.key_algorithm,
        protector=protector,
    )
    _migrate_signing_key(keystore, database, clock)
    export_policy = ExportPolicy(settings.exports.encrypt)
    report_service = ReportService(
        auth,
        database,
        data_dir=settings.data_dir,
        keystore=keystore,
        export_policy=export_policy,
        media=media,
        copyright_holder=reports.copyright_holder,
        include_images=reports.include_images,
        clock=clock,
    )
    experiments = ExperimentService(
        auth, database, reagents, engine, instrument_no=settings.instrument_no, clock=clock
    )
    applier = _RuntimeApplier(
        engine, pipeline, experiments, report_service, settings, export_policy
    )
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
        exports=ExportService(
            auth,
            database,
            clock=clock,
            policy=export_policy,
        ),
        settings=InstrumentSettingsService(
            auth,
            database,
            engine,
            base_config=base_config,
            effective_config=settings,
            apply=applier.apply,
            source_factory=create_video_source,
            live_probe=lambda: _probe_running_pipeline(pipeline),
            clock=clock,
        ),
        base_config=base_config,
        demo_accounts=tuple(hook.accounts()) if hook is not None else (),
        first_run=first_run,
        key_protector=protector,
        audit_keys=audit_keys,
        license=_license_service(settings),
        export_policy=export_policy,
    )
    applier.attach(context)
    return context


def _cleanup_startup_resource(
    name: str, cleanup: Callable[[], object], engine_stopped: list[bool]
) -> None:
    """释放未完成组装时取得的资源，不覆盖最初的异常。"""
    if not engine_stopped[0]:
        _log.error("试验引擎未退出，跳过清理%s以避免与工作线程并发", name)
        return
    try:
        cleanup()
    except Exception:
        _log.exception("清理未完成启动时的%s资源失败", name)


def _cleanup_engine_startup(
    engine: ExperimentEngine, timeout_s: float, engine_stopped: list[bool]
) -> None:
    """停止未完成组装的引擎；超过时限未退出时记录错误并保留依赖资源。"""
    try:
        engine_stopped[0] = engine.shutdown(timeout_s)
        if not engine_stopped[0]:
            _log.error("清理未完成启动时的试验引擎超时；仍在使用的资源将保留")
    except Exception:
        engine_stopped[0] = False
        _log.exception("清理未完成启动时的试验引擎失败")


def key_protection_mode(config: AppConfig) -> ProtectionMode:
    """密钥保护方式：环境变量 ``GELINSPECT_KEY_PROTECTION`` 优先，其次配置。

    Args:
        config: 配置。

    Returns:
        ``auto``、``os`` 或 ``file``。

    Raises:
        ConfigError: 环境变量的值不认识。
    """
    override = os.environ.get(KEY_PROTECTION_ENV, "").strip()
    if not override:
        return config.security.key_protection
    if override not in ("auto", "os", "file"):
        raise ConfigError(f"环境变量 {KEY_PROTECTION_ENV} 只能是 auto、os 或 file：{override!r}")
    return cast(ProtectionMode, override)


def _migrate_signing_key(
    keystore: LocalKeyStore, database: Database, clock: Callable[[], datetime] | None
) -> None:
    """启动时把明文私钥加密保存，并写审计。私钥损坏不阻止启动（签署时会报错说明）。"""
    try:
        migrated = keystore.migrate()
    except KeyStoreError as exc:
        _log.error("签名私钥无法迁移：%s", exc)
        return
    if migrated is None:
        return
    detail = (
        "报告签名私钥由明文改为加密保存，原明文文件已覆盖删除"
        if migrated == "plaintext"
        else "报告签名私钥改用新的保护方式重新加密"
    )
    database.audit.append(
        AuditEvent(
            occurred_at=(clock or (lambda: datetime.now(UTC)))(),
            actor=SYSTEM_ACTOR,
            actor_role=None,
            action=AuditAction.KEY_PROTECTION_CHANGED,
            detail=detail,
        )
    )


def _license_service(config: AppConfig) -> LicenseService:
    """按配置创建授权服务（默认不启用）。"""
    section = config.license
    path = section.file
    if path is None:
        path = config.data_dir / LICENSE_FILE
    elif not path.is_absolute():
        path = config.data_dir / path
    return LicenseService(enabled=section.enabled, license_file=path)
