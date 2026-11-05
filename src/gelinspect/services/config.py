"""读取 YAML 配置，并按 ``包.模块:类名`` 加载后端。

不指定配置文件时使用 :data:`DEFAULT_CONFIG`，它指向 ``simulation/``，所以在仓库里
``uv run gelinspect`` 可以一键启动。主代码只持有这些字符串，不导入 ``simulation``。
配置文件是完整的配置，不与默认配置合并：切换到真实设备时，漏写的项不会悄悄沿用模拟后端
或时间加速。后端加载失败时报错，**绝不回退到模拟实现**。
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.interfaces.video import VideoConfig
from gelinspect.services.engine_types import EngineConfig
from gelinspect.services.video.config import (
    BufferConfig,
    PreviewConfig,
    QualityConfig,
    ReconnectConfig,
    RecorderConfig,
    SnapshotConfig,
    VideoPipelineConfig,
)

T = TypeVar("T")

DATABASE_FILE = "gelinspect.db"
"""数据库文件名，放在数据目录下。"""

_FILE_SOURCES = frozenset({"file", "images"})

DEFAULT_CONFIG: Mapping[str, Any] = {
    "data_dir": "data",
    "analysis": {
        "backend": "simulation.mock_analyzer:MockAnalyzer",
        "config_path": "simulation/analyzer.example.yaml",
        "timeout_ms": 2000,
    },
    "video": {
        "source": "simulation.sim_video:SimVideoSource",
        "device": "sim",
        "width": 1920,
        "height": 1080,
        "fps": 15,
    },
    "device": {"backend": "simulation.sim_device:SimDevice"},
    "engine": {"incubation_minutes": 60, "time_scale": 60},
    "bootstrap": {"hook": "simulation.demo_accounts:DemoAccounts"},
    "traceability": {
        "parser": {"backend": "simulation.reagents:SimReagentCodeParser"},
        "registry": {"backend": "simulation.reagents:SimReagentRegistry"},
        "government": {"backend": "simulation.reagents:SimTraceabilityClient"},
    },
}
"""默认配置：模拟后端，时间加速 60 倍，首次启动创建演示账号，试剂码和备案清单用模拟白名单。"""


class ConfigError(Exception):
    """配置有误，或后端无法加载。信息面向部署人员，说明要改什么。"""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalysisSection(_Section):
    """算法后端配置。

    Attributes:
        backend: 类路径。
        config_path: 传给 ``GelAnalyzer.load`` 的文件，可为空。
        timeout_ms: ``analyze_row`` 的时限，超时只告警。
        options: 构造函数的关键字参数。
    """

    backend: str = Field(min_length=1)
    config_path: Path | None = None
    timeout_ms: int = Field(default=2000, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)


class VideoSection(_Section):
    """视频源和视频管线配置。

    Attributes:
        source: 类路径，或别名 ``file``、``images``。
        device: 视频文件、图片文件夹或相机序列号。
        width: 期望宽度。
        height: 期望高度。
        fps: 期望帧率。
        read_timeout_ms: ``read`` 的最长阻塞时间。
        options: 构造函数的关键字参数。
        buffer: 环形缓冲。
        preview: 预览。
        recorder: 分段录制。
        snapshot: 抓帧。
        quality: 质量检查。
        reconnect: 重连策略。
        open_timeout_s: 打开视频源的时限。
        stop_timeout_s: 停止时等待每个线程的时限。
    """

    source: str = Field(min_length=1)
    device: str = Field(min_length=1)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    read_timeout_ms: int = Field(default=1000, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)
    buffer: BufferConfig = BufferConfig()
    preview: PreviewConfig = PreviewConfig()
    recorder: RecorderConfig = RecorderConfig()
    snapshot: SnapshotConfig = SnapshotConfig()
    quality: QualityConfig = QualityConfig()
    reconnect: ReconnectConfig = ReconnectConfig()
    open_timeout_s: float = Field(default=30.0, gt=0)
    stop_timeout_s: float = Field(default=5.0, gt=0)


class DeviceSection(_Section):
    """设备后端配置。

    Attributes:
        backend: 类路径。
        options: 构造函数的关键字参数。
    """

    backend: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class SecuritySection(_Section):
    """账号安全配置。

    Attributes:
        idle_lock_minutes: 空闲多久自动锁屏，0 表示不自动锁屏。
        max_login_attempts: 连续失败多少次后锁定。
        lockout_minutes: 锁定时长。
    """

    idle_lock_minutes: float = Field(default=15.0, ge=0)
    max_login_attempts: int = Field(default=5, ge=1)
    lockout_minutes: float = Field(default=5.0, gt=0)


class BootstrapSection(_Section):
    """首次启动的初始化钩子。

    Attributes:
        hook: 实现 ``BootstrapHook`` 的类路径。留空表示没有默认账号，
            首次启动时在界面创建管理员。
        options: 构造函数的关键字参数。
    """

    hook: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class BackendRef(_Section):
    """一个可替换后端的引用。

    Attributes:
        backend: 类路径 ``包.模块:类名``。
        options: 构造函数的关键字参数。
    """

    backend: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class TraceabilitySection(_Section):
    """试剂溯源：试剂码解析、备案清单和政府溯源平台，三者都可替换。

    试剂码格式和备案清单尚未提供（``docs/PLAN.md`` 第 7 节第 6 项），所以没有内置实现；
    不配置解析器或备案清单时，试剂无法校验，向导停在「校验试剂」并说明原因，**不会放行**。

    Attributes:
        parser: 实现 ``ReagentCodeParser`` 的后端。
        registry: 实现 ``ReagentRegistry`` 的后端。
        government: 实现 ``GovernmentTraceabilityClient`` 的后端，未配置表示未接入。
    """

    parser: BackendRef | None = None
    registry: BackendRef | None = None
    government: BackendRef | None = None


class ReportSection(_Section):
    """报告与数字签名配置。

    Attributes:
        copyright_holder: 版权声明里的权利人。正式名称未定（PLAN 第 7 节第 7 项），
            默认是占位文字，写入每份报告的页脚。
        certificate_common_name: 本地自签证书的通用名（CN）。
        certificate_organization: 本地自签证书的组织名（O）。
        certificate_valid_days: 证书有效天数。已签署的报告在证书过期后仍可验签的前提是
            验签时刻取签署时刻，目前按「当前时刻」验证，所以请设得足够长。
        key_algorithm: 自签证书的密钥算法：``ecdsa``（P-256）或 ``rsa``（3072 位）。
        include_images: 报告是否附每排 8 连图缩略页。图是读盘解码的，12 MP 下较慢，默认关闭。
    """

    copyright_holder: str = Field(default="[版权方名称]", min_length=1, max_length=120)
    certificate_common_name: str = Field(
        default="GelInspect Report Signing", min_length=1, max_length=64
    )
    certificate_organization: str = Field(default="GelInspect", min_length=1, max_length=64)
    certificate_valid_days: int = Field(default=3650, ge=30, le=36500)
    key_algorithm: Literal["ecdsa", "rsa"] = "ecdsa"
    include_images: bool = False


class AppConfig(_Section):
    """软件的完整配置。加载后路径都已是绝对路径。

    Attributes:
        data_dir: 数据目录，存放数据库、视频和图像。
        analysis: 算法后端。
        video: 视频。
        device: 设备后端。
        engine: 试验引擎。``analysis_timeout_s`` 由 ``analysis.timeout_ms`` 决定。
        interpretation: 三态到阴阳性的映射，未写的状态用默认值。
        low_confidence_threshold: 低置信度阈值（0–1），低于它的结果转待复核；默认不启用，
            等 QA 确认（PLAN 第 7 节第 17 项）。
        security: 账号安全。
        bootstrap: 首次启动初始化钩子。
        traceability: 试剂码解析、备案清单和政府溯源平台。
        instrument_no: 仪器编号，写入每个新试验。
        reports: 报告与数字签名。
    """

    data_dir: Path = Path("data")
    analysis: AnalysisSection
    video: VideoSection
    device: DeviceSection
    engine: EngineConfig = EngineConfig()
    interpretation: dict[str, str] = Field(default_factory=dict)
    low_confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    security: SecuritySection = SecuritySection()
    bootstrap: BootstrapSection = BootstrapSection()
    traceability: TraceabilitySection = TraceabilitySection()
    instrument_no: str = Field(default="GI-001", min_length=1, max_length=64)
    reports: ReportSection = ReportSection()

    @property
    def reports_dir(self) -> Path:
        """已签署报告的目录，每个试验一个子目录。"""
        return self.data_dir / "reports"

    @property
    def keys_dir(self) -> Path:
        """签名证书和私钥的目录（已被 ``.gitignore`` 忽略）。"""
        return self.data_dir / "keys"

    @property
    def database_path(self) -> Path:
        """数据库文件路径。"""
        return self.data_dir / DATABASE_FILE

    def engine_config(self) -> EngineConfig:
        """生成引擎配置，算法时限取自 ``analysis.timeout_ms``。

        Returns:
            引擎配置。
        """
        return self.engine.model_copy(
            update={"analysis_timeout_s": self.analysis.timeout_ms / 1000}
        )

    def pipeline_config(self) -> VideoPipelineConfig:
        """生成视频管线配置。

        Returns:
            视频管线配置。
        """
        video = self.video
        return VideoPipelineConfig(
            data_dir=self.data_dir,
            source=VideoConfig(
                device=video.device,
                width=video.width,
                height=video.height,
                fps=video.fps,
                read_timeout_ms=video.read_timeout_ms,
            ),
            buffer=video.buffer,
            preview=video.preview,
            recorder=video.recorder,
            snapshot=video.snapshot.model_copy(
                update={"frames_per_side": self.engine.frames_per_side}
            ),
            quality=video.quality,
            reconnect=video.reconnect,
            open_timeout_s=video.open_timeout_s,
            stop_timeout_s=video.stop_timeout_s,
        )

    def mapping(self) -> GelStateMapping:
        """生成三态映射。

        Returns:
            映射规则。
        """
        return GelStateMapping.from_config(
            self.interpretation, low_confidence_threshold=self.low_confidence_threshold
        )


def project_root() -> Path:
    """定位仓库根目录：从本文件向上找 ``pyproject.toml``，找不到时用当前目录。

    Returns:
        相对路径的基准目录。
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def parse_config(data: Mapping[str, Any], *, base_dir: Path | None = None) -> AppConfig:
    """校验配置字典，并把相对路径转成绝对路径。

    Args:
        data: 配置内容。
        base_dir: 相对路径的基准，默认仓库根目录，与当前工作目录无关。

    Returns:
        配置。

    Raises:
        ConfigError: 字段缺失、类型不对、有未知字段，或文件不存在。
    """
    try:
        config = AppConfig.model_validate(dict(data))
        config.mapping()
    except (ValidationError, ValueError, TypeError) as exc:
        raise ConfigError(f"配置内容有误：{exc}") from exc
    base = base_dir if base_dir is not None else project_root()
    analysis = config.analysis
    if analysis.config_path is not None:
        config_path = base / analysis.config_path
        if not config_path.is_file():
            raise ConfigError(
                f"analysis.config_path 指向的文件不存在：{config_path}。"
                "如果它属于 simulation/，请恢复该目录，或改成真实算法的配置"
            )
        analysis = analysis.model_copy(update={"config_path": config_path})
    video = config.video
    if video.source in _FILE_SOURCES:
        video = video.model_copy(update={"device": str(base / video.device)})
    return config.model_copy(
        update={"data_dir": base / config.data_dir, "analysis": analysis, "video": video}
    )


def load_config(path: Path | None = None, *, base_dir: Path | None = None) -> AppConfig:
    """读取配置。

    Args:
        path: YAML 文件，None 时使用 :data:`DEFAULT_CONFIG`。文件内容就是完整配置，不与默认值合并。
        base_dir: 相对路径的基准，默认仓库根目录。

    Returns:
        配置。

    Raises:
        ConfigError: 文件读不了、不是 YAML 字典，或内容有误。
    """
    if path is None:
        return parse_config(DEFAULT_CONFIG, base_dir=base_dir)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取配置文件 {path}：{exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 {path} 不是合法的 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {path} 的顶层必须是字典")
    return parse_config(data, base_dir=base_dir)


def explain_import_failure(setting: str, spec: str, error: BaseException) -> str:
    """把后端的导入失败说成部署人员能处理的话。

    Args:
        setting: 配置项名称，例如 ``device.backend``。
        spec: 配置的类路径。
        error: 导入时的异常。

    Returns:
        错误说明。
    """
    missing = error.name if isinstance(error, ModuleNotFoundError) else None
    if missing is not None and missing.split(".")[0] == "simulation":
        return (
            f"{setting} 指向模拟实现 {spec!r}，但找不到 simulation 包"
            "（可能已从发布包排除或被移走）。请恢复 simulation/ 目录，或在配置文件里把 "
            "analysis.backend、video.source、device.backend 改成真实实现。"
        )
    return f"{setting} 无法导入 {spec!r}：{error}"


def load_backend(setting: str, spec: str, protocol: type[T], options: Mapping[str, Any]) -> T:
    """按类路径创建后端实例，并检查它满足接口。

    Args:
        setting: 配置项名称，写入错误信息。
        spec: ``包.模块:类名`` 或 ``包.模块.类名``。
        protocol: 后端要满足的 ``runtime_checkable`` 接口。
        options: 构造函数的关键字参数。

    Returns:
        后端实例，尚未连接硬件或加载模型。

    Raises:
        ConfigError: 格式不对、导入失败、构造失败或不满足接口。
    """
    module_name, sep, class_name = spec.strip().partition(":")
    if not sep:
        module_name, _, class_name = spec.strip().rpartition(".")
    if not module_name or not class_name:
        raise ConfigError(f"{setting} 的格式应为 '包.模块:类名'：{spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(explain_import_failure(setting, spec, exc)) from exc
    backend_class = getattr(module, class_name, None)
    if not isinstance(backend_class, type):
        raise ConfigError(f"{setting}：{module_name} 中没有名为 {class_name} 的类")
    try:
        instance = backend_class(**dict(options))
    except Exception as exc:
        raise ConfigError(f"{setting}：创建 {spec} 失败：{exc!r}") from exc
    if not isinstance(instance, protocol):
        raise ConfigError(f"{setting}：{spec} 没有实现 {protocol.__name__}")
    return instance
