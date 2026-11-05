"""仪器设置：查看、校验、保存、预览视频输入。

权限：任何登录用户可以**查看**当前设置（``experiment_view``）；**修改**和预览别的视频路径需要
``instrument_configure``（管理员），服务层每次都重新校验，界面只是把输入框置灰。

规则：

* 保存前整体校验，所有不合法的项一次报出（:class:`SettingsError`），一项都不会写。
* **试验进行中不能改影响运行的项**（孵育时间、读数参数、温度、视频输入）；仪器编号、保留天数和
  版权方名称随时可改。运行中提交这类改动会被拒绝，什么都不改。
* 保存分三步：运行时生效 → 写用户配置文件（``data/`` 下，不改仓库里的默认配置）→ 写审计
  （``settings_changed``，逐项记录旧值和新值）。任何一步失败，前面的步骤都撤销，不会留下
  「界面显示一套、文件里另一套」的状态。
* 改动对**之后启动的试验**生效；视频输入换成新的视频文件或图片文件夹后，下一个试验用的就是它。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import ExperimentRepository, UnitOfWork
from gelinspect.interfaces.video import VideoConfig, VideoError, VideoSource
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.config import AppConfig, ConfigError
from gelinspect.services.engine_types import EngineBusyError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.user_settings import (
    FIELD_NAMES,
    IMPACTING_FIELDS,
    InstrumentSettings,
    SettingsDraft,
    SettingsProblem,
    VideoInput,
    apply_settings,
    check_video_path,
    overrides_between,
    parse_draft,
    settings_file,
    settings_of,
    write_overrides,
)
from gelinspect.services.video.errors import PipelineStateError

_log = logging.getLogger(__name__)

RuntimeApplier = Callable[[AppConfig], None]
"""把新配置应用到运行中的引擎、视频管线等；失败抛 ``ConfigError``、``EngineBusyError`` 或
``PipelineStateError``，且不留下半个状态。由组装根提供。"""

SourceFactory = Callable[[str, Mapping[str, Any]], VideoSource]
"""按类路径或别名创建视频源（尚未打开）。由组装根提供，服务层不导入具体实现。"""


class SettingsEngine(Protocol):
    """设置服务需要知道的引擎状态。"""

    @property
    def active_experiment_id(self) -> str | None:
        """正在运行的试验。"""
        ...


class SettingsStorage(Protocol):
    """设置服务需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储（读最近的算法版本）。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


class SettingsError(ServiceError):
    """有设置项不合法。

    Attributes:
        problems: 每个不合法的项和原因。
    """

    def __init__(self, problems: list[SettingsProblem]) -> None:
        """创建异常。

        Args:
            problems: 不合法的项，至少一项。
        """
        super().__init__(
            "设置有误：" + "；".join(p.describe() for p in problems),
            ServiceErrorCode.SETTINGS_INVALID,
        )
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class BackendInfo:
    """算法等后端的只读信息。

    Attributes:
        analysis_backend: 算法后端的类路径。
        analysis_config_path: 算法配置文件。
        model_version: 最近一次分析所用的模型版本；还没有跑过试验时为 None。
        config_version: 最近一次分析所用的阈值配置版本。
        device_backend: 设备后端的类路径。
        video_source: 视频源的类路径或别名。
    """

    analysis_backend: str
    analysis_config_path: str | None
    model_version: str | None
    config_version: str | None
    device_backend: str
    video_source: str


@dataclass(frozen=True, slots=True)
class SettingsView:
    """设置页要显示的内容。

    Attributes:
        settings: 生效的设置。
        defaults: 默认配置下的设置（没有用户改动时的值）。
        backend: 后端信息。
        running_experiment: 正在运行的试验；不为 None 时影响运行的项不能改。
    """

    settings: InstrumentSettings
    defaults: InstrumentSettings
    backend: BackendInfo
    running_experiment: str | None


@dataclass(frozen=True, slots=True)
class SaveResult:
    """保存的结果。

    Attributes:
        settings: 保存后生效的设置。
        changed: 实际改动的项（设置项名）；没有改动时为空，此时什么都没有写。
    """

    settings: InstrumentSettings
    changed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VideoProbe:
    """视频输入的预览：第一帧和基本参数。

    Attributes:
        width: 分辨率宽。
        height: 分辨率高。
        fps: 帧率，未知时为 None。
        frame_count: 总帧数（文件或图片数），未知时为 None。
        description: 来源描述。
        frame: 第一帧（BGR），读不到时为 None。
    """

    width: int
    height: int
    fps: float | None
    frame_count: int | None
    description: str
    frame: NDArray[np.uint8] | None


_FIELD_LABELS: dict[str, str] = {
    "instrument_no": "仪器编号",
    "incubation_minutes": "孵育时间（分钟）",
    "frames_per_side": "翻转前后各取帧数",
    "settle_s": "翻转后稳定时间（秒）",
    "temperature_target_c": "目标温度（℃）",
    "temperature_tolerance_c": "允许偏差（℃）",
    "temperature_hold_s": "越限告警持续时间（秒）",
    "video_input": "视频输入",
    "video_path": "视频路径",
    "retention_days": "数据保留天数",
    "copyright_holder": "版权方名称",
    "encrypt_exports": "导出加密",
}
_INPUT_LABELS = {
    VideoInput.CONFIG: "沿用配置文件",
    VideoInput.FILE: "视频文件",
    VideoInput.IMAGES: "图片文件夹",
}


def _show(name: str, value: object) -> str:
    if name == "video_input" and isinstance(value, VideoInput):
        return _INPUT_LABELS[value]
    if name == "retention_days" and value is None:
        return "不自动删除"
    if name == "video_path" and value == "":
        return "（无）"
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


PREVIEW_LONG_SIDE = 960
"""预览图的长边像素数：12 MP 的原图不必整张送到界面。"""


def _shrink(frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
    long_side = max(frame.shape[:2])
    if long_side <= PREVIEW_LONG_SIDE:
        return frame
    scale = PREVIEW_LONG_SIDE / long_side
    size = (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale)))
    return np.asarray(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


class InstrumentSettingsService:
    """仪器设置服务。保存时串行，线程安全。"""

    def __init__(
        self,
        auth: AuthService,
        storage: SettingsStorage,
        engine: SettingsEngine,
        *,
        base_config: AppConfig,
        effective_config: AppConfig,
        apply: RuntimeApplier,
        source_factory: SourceFactory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限。
            storage: 存储。
            engine: 引擎（只读它是否有试验在运行）。
            base_config: 没有叠加用户设置的默认配置。
            effective_config: 启动时已叠加用户设置的配置。
            apply: 把新配置应用到运行中的各部分。
            source_factory: 创建视频源，用来预览视频输入。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._engine = engine
        self._base = base_config
        self._effective = effective_config
        self._apply = apply
        self._source_factory = source_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._path = settings_file(base_config.data_dir)

    # ------------------------------------------------------------------ 查看

    @property
    def settings_path(self) -> Path:
        """用户配置文件的路径。"""
        return self._path

    def current(self, session: Session | None) -> SettingsView:
        """当前生效的设置、默认值和后端信息。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限（所有角色）。

        Returns:
            设置页内容。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return SettingsView(
            settings=settings_of(self._effective),
            defaults=settings_of(self._base),
            backend=self.backend_info(),
            running_experiment=self._engine.active_experiment_id,
        )

    def backend_info(self) -> BackendInfo:
        """算法、设备和视频后端的信息；模型和配置版本取自最近一次分析的记录。

        Returns:
            后端信息。
        """
        config = self._effective
        versions = self._storage.experiments.latest_analyzer_versions()
        return BackendInfo(
            analysis_backend=config.analysis.backend,
            analysis_config_path=(
                None if config.analysis.config_path is None else str(config.analysis.config_path)
            ),
            model_version=None if versions is None else versions[0],
            config_version=None if versions is None else versions[1] or None,
            device_backend=config.device.backend,
            video_source=config.video.source,
        )

    def validate(self, session: Session | None, draft: SettingsDraft) -> list[SettingsProblem]:
        """校验输入框的内容，不保存。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            draft: 输入框内容。

        Returns:
            不合法的项；空表示都合法。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return parse_draft(draft, current=self._current_draft())[1]

    def _current_draft(self) -> SettingsDraft:
        return SettingsDraft.from_settings(settings_of(self._effective))

    # ------------------------------------------------------------------ 保存

    def save(self, session: Session | None, draft: SettingsDraft) -> SaveResult:
        """校验并保存设置，立即对之后的试验生效。

        Args:
            session: 调用者的会话，需要 ``instrument_configure`` 权限。
            draft: 输入框内容。

        Returns:
            保存的结果；没有任何改动时 ``changed`` 为空，什么都不写。

        Raises:
            PermissionDeniedError: 没有权限。
            SettingsError: 有设置项不合法。
            ServiceError: 试验进行中不能改影响运行的项（``SETTINGS_BUSY``），或写文件失败
                （``SETTINGS_SAVE_FAILED``，已撤销）。
            StorageBusyError: 数据库忙，审计没有写入，设置已撤销，可重试。
        """
        user = self._auth.authorize(session, Permission.INSTRUMENT_CONFIGURE)
        new, problems = parse_draft(draft, current=self._current_draft())
        if new is None:
            raise SettingsError(problems)
        with self._lock:
            old = settings_of(self._effective)
            changed = tuple(n for n in FIELD_NAMES if getattr(old, n) != getattr(new, n))
            if not changed:
                return SaveResult(old, ())
            running = self._engine.active_experiment_id
            if running is not None and IMPACTING_FIELDS.intersection(changed):
                raise ServiceError(
                    f"试验 {running} 正在运行，现在不能修改影响运行的设置",
                    ServiceErrorCode.SETTINGS_BUSY,
                    id=running,
                )
            new_config = apply_settings(self._base, new)
            self._commit(new_config, new)
            detail = "修改仪器设置：" + "；".join(
                f"{_FIELD_LABELS[n]} {_show(n, getattr(old, n))} → {_show(n, getattr(new, n))}"
                for n in changed
            )
            try:
                with self._storage.unit_of_work() as uow:
                    uow.audit.append(
                        AuditEvent(
                            occurred_at=self._clock(),
                            actor=user.username,
                            actor_role=user.role,
                            action=AuditAction.SETTINGS_CHANGED,
                            detail=detail,
                        )
                    )
            except BaseException:
                self._rollback(old)
                raise
            self._effective = new_config
            return SaveResult(new, changed)

    def _commit(self, new_config: AppConfig, new: InstrumentSettings) -> None:
        """运行时生效，再写文件；失败就撤销已做的部分。"""
        try:
            self._apply(new_config)
        except (ConfigError, EngineBusyError, PipelineStateError) as error:
            code = (
                ServiceErrorCode.SETTINGS_BUSY
                if isinstance(error, EngineBusyError | PipelineStateError)
                else ServiceErrorCode.SETTINGS_SAVE_FAILED
            )
            raise ServiceError(f"设置没有生效：{error}", code, reason=str(error)) from error
        try:
            write_overrides(self._path, overrides_between(settings_of(self._base), new))
        except OSError as error:
            self._apply_quietly(self._effective)
            raise ServiceError(
                f"无法写入用户设置文件 {self._path}：{error}",
                ServiceErrorCode.SETTINGS_SAVE_FAILED,
                reason=str(error),
            ) from error

    def _rollback(self, old: InstrumentSettings) -> None:
        """审计没写进去：撤销运行时设置和文件。"""
        self._apply_quietly(self._effective)
        try:
            write_overrides(self._path, overrides_between(settings_of(self._base), old))
        except OSError:
            _log.exception("撤销用户设置文件失败：%s", self._path)

    def _apply_quietly(self, config: AppConfig) -> None:
        try:
            self._apply(config)
        except (ConfigError, EngineBusyError, PipelineStateError):
            _log.exception("撤销运行时设置失败")

    # ------------------------------------------------------------------ 预览视频输入

    def preview_video(
        self,
        session: Session | None,
        kind: VideoInput | None = None,
        path: str | None = None,
    ) -> VideoProbe:
        """读取视频输入的第一帧和参数，用于设置页预览。会读文件，应放在后台线程调用。

        不带参数时预览当前生效的视频源（所有角色可看）；指定别的路径需要
        ``instrument_configure``。预览不改变任何设置，也不影响运行中的试验。

        Args:
            session: 调用者的会话。
            kind: 视频输入类型；None 表示当前生效的。
            path: 视频文件或图片文件夹。

        Returns:
            第一帧和参数。

        Raises:
            PermissionDeniedError: 没有权限。
            SettingsError: 路径不合法。
            ServiceError: 打不开或读不到画面（``SETTINGS_PREVIEW_FAILED``）。
        """
        if kind is None:
            self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
            video = self._effective.video
            spec, options, device = video.source, video.options, video.device
            config = VideoConfig(
                device=device, width=video.width, height=video.height, fps=video.fps
            )
        else:
            self._auth.authorize(session, Permission.INSTRUMENT_CONFIGURE)
            if kind is VideoInput.CONFIG:
                video = self._base.video  # 「沿用配置文件」预览的是默认配置的视频源
                config = VideoConfig(
                    device=video.device, width=video.width, height=video.height, fps=video.fps
                )
                return self._probe(video.source, video.options, config)
            problems: list[SettingsProblem] = []
            device = check_video_path(kind, path or "", problems)
            if problems:
                raise SettingsError(problems)
            spec, options = kind.value, {}
            config = VideoConfig(device=device)
        return self._probe(spec, options, config)

    def _create_source(self, spec: str, options: Mapping[str, Any]) -> VideoSource:
        """创建预览用的视频源：尽快读完（不按帧率限速）；不认这个选项的视频源按原样创建。"""
        try:
            return self._source_factory(spec, {**options, "realtime": False})
        except VideoError:
            return self._source_factory(spec, options)

    def _probe(self, spec: str, options: Mapping[str, Any], config: VideoConfig) -> VideoProbe:
        safe = {k: v for k, v in options.items() if k != "loop"}
        try:
            source = self._create_source(spec, safe)
        except VideoError as error:
            raise ServiceError(
                f"无法创建视频源：{error}",
                ServiceErrorCode.SETTINGS_PREVIEW_FAILED,
                reason=str(error),
            ) from error
        try:
            info = source.open(config)
            frame: NDArray[np.uint8] | None = None
            for _ in range(10):
                got = source.read()
                if got is not None:
                    frame = got.image
                    break
        except VideoError as error:
            raise ServiceError(
                f"无法读取视频输入：{error}",
                ServiceErrorCode.SETTINGS_PREVIEW_FAILED,
                reason=str(error),
            ) from error
        finally:
            source.close()
        return VideoProbe(
            width=info.width,
            height=info.height,
            fps=info.fps,
            frame_count=info.frame_count,
            description=info.description,
            frame=None if frame is None else _shrink(frame),
        )
