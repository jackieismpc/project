"""界面需要的全部服务，由组装根创建后交给主窗口。

界面只依赖本模块列出的对象，不接触数据库、设备和视频管线。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gelinspect.interfaces.bootstrap import DemoAccount
from gelinspect.services.audit_service import AuditService
from gelinspect.services.auth import AuthService
from gelinspect.services.dashboard_service import DashboardService
from gelinspect.services.engine_types import RecoveredExperiment
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.experiment_service import ExperimentService
from gelinspect.services.export_service import ExportService
from gelinspect.services.instrument_monitor import InstrumentMonitor
from gelinspect.services.media_service import MediaService
from gelinspect.services.monitor_service import MonitorService
from gelinspect.services.queries import QueryService
from gelinspect.services.reagent_service import ReagentService
from gelinspect.services.reporting.report_service import ReportService
from gelinspect.services.review_service import ReviewService
from gelinspect.services.settings_service import InstrumentSettingsService
from gelinspect.services.template_service import TemplateService
from gelinspect.services.user_service import UserService


@dataclass(frozen=True, slots=True)
class AppServices:
    """界面使用的服务集合。

    Attributes:
        auth: 登录与权限。
        queries: 只读查询。
        engine: 试验引擎（启动、停止、事件订阅、退出确认）。
        instrument: 仪器状态监视器。
        templates: 检测模板管理。
        reagents: 试剂扫码校验与溯源。
        experiments: 创建和启动试验。
        monitor: 试验监控（视图、停止、预览和视频事件）。
        media: 原图、掩码和录像。
        reviews: 单管人工复核。
        dashboard: 工作台数据。
        reports: 报告中心（预览、签署、验签、导出）。
        users: 用户与权限管理。
        audit: 审计追踪（查询、校验哈希链）。
        exports: Excel 导出（试验结果、历史列表、审计）。
        settings: 仪器设置。
        demo_accounts: 登录页“演示账号”提示的内容，真实配置下为空。
        idle_lock_minutes: 空闲多久自动锁屏，0 表示不锁。
        recovered: 启动恢复时被标记为异常的试验。
        data_dir: 数据目录。
        log_dir: 日志目录。
        backends: 已加载的后端类路径（关于页显示）。
    """

    auth: AuthService
    queries: QueryService
    engine: ExperimentEngine
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
    demo_accounts: Sequence[DemoAccount] = ()
    idle_lock_minutes: float = 15.0
    recovered: Sequence[RecoveredExperiment] = ()
    data_dir: Path = Path("data")
    log_dir: Path = Path("data/logs")
    backends: Mapping[str, str] = field(default_factory=dict)
