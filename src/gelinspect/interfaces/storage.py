"""存储接口：仓储 ``Protocol`` 与分页、查询、记录等数据契约。

服务层只依赖本模块，不导入 ``infrastructure.db`` 的具体类。仓储实现与权限无关：
是否有权限执行某个操作由服务层用 ``domain.permissions`` 检查。

审计仓储只提供追加和查询，**不提供更新和删除**。

单个仓储方法各自是一个事务。需要让多个写入一起生效时使用 :class:`UnitOfWork`：
试验状态、单管结果、媒体记录和审计事件在同一事务提交，要么都生效，要么都不生效。
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from gelinspect.domain.entities import AuditEvent, Experiment, TubeResult, User
from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import ExperimentTemplate

T = TypeVar("T")

MAX_PAGE_SIZE = 1000
"""单页最大条数。"""

GENESIS_HASH = "0" * 64
"""审计哈希链第一条记录的 ``prev_hash``。"""


class StorageError(Exception):
    """存储层异常基类。"""


class NotFoundError(StorageError):
    """要操作的记录不存在。"""


class DuplicateError(StorageError):
    """记录已存在，违反唯一性约束。"""


class DatabaseVersionError(StorageError):
    """数据库版本比当前程序更新，或迁移失败。"""


class StateConflictError(StorageError):
    """试验在库中的状态与调用方预期不符，说明有别的写入者改过它。"""


@dataclass(frozen=True, slots=True)
class PageRequest:
    """分页请求。

    Attributes:
        offset: 跳过的条数，不小于 0。
        limit: 本页最多条数，1 到 :data:`MAX_PAGE_SIZE`。
    """

    offset: int = 0
    limit: int = 50

    def __post_init__(self) -> None:
        """校验分页参数。

        Raises:
            ValueError: ``offset`` 为负或 ``limit`` 超出范围。
        """
        if self.offset < 0:
            raise ValueError(f"offset 不能为负：{self.offset}")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit 应在 1–{MAX_PAGE_SIZE} 内：{self.limit}")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):  # noqa: UP046
    """分页结果。

    Attributes:
        items: 本页记录。
        total: 满足条件的总条数。
        request: 产生本页的请求。
    """

    items: tuple[T, ...]
    total: int
    request: PageRequest


@dataclass(frozen=True, slots=True)
class UserCredentials:
    """登录校验所需的用户信息。

    Attributes:
        user: 用户。
        password_hash: argon2 密码哈希。
        must_change_password: 首次登录是否必须修改密码。
    """

    user: User
    password_hash: str
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """已入库的审计记录。

    Attributes:
        seq: 序号，从 1 开始连续递增。
        event: 审计事件，时间统一为 UTC。
        prev_hash: 上一条记录的 ``hash``，第一条为 :data:`GENESIS_HASH`。
        hash: 本条记录的 SHA-256（十六进制）。
    """

    seq: int
    event: AuditEvent
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """审计查询条件，条件之间为「且」，为 None 的条件不生效。

    Attributes:
        start: 起始时间（含）。
        end: 结束时间（不含）。
        actor: 操作者用户名，精确匹配。
        keyword: 在操作记录和试验标识中做子串匹配，不区分大小写。
        action: 事件类型。
        experiment_id: 关联试验，精确匹配。
    """

    start: datetime | None = None
    end: datetime | None = None
    actor: str | None = None
    keyword: str | None = None
    action: AuditAction | None = None
    experiment_id: str | None = None


class ChainFailure(StrEnum):
    """哈希链校验失败的原因。"""

    HASH_MISMATCH = "hash_mismatch"
    """记录内容与其 ``hash`` 不符，内容被改动。"""
    LINK_BROKEN = "link_broken"
    """``prev_hash`` 与上一条的 ``hash`` 不符。"""
    MISSING = "missing"
    """序号不连续或尾部缺失，有记录被删除。"""
    HEAD_MISMATCH = "head_mismatch"
    """链头记录与最后一条记录不符。"""


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """哈希链校验结果。

    Attributes:
        ok: 整条链是否完好。
        checked: 已校验的记录条数。
        first_bad_seq: 第一处异常的序号。记录被删除时是缺失的序号；
            链头缺失时为 None。
        failure: 异常原因，完好时为 None。
        message: 面向用户的说明。
    """

    ok: bool
    checked: int
    first_bad_seq: int | None = None
    failure: ChainFailure | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ExperimentQuery:
    """历史试验查询条件，条件之间为「且」。

    Attributes:
        experiment_type: 试验类型。
        start: 创建时间起点（含）。
        end: 创建时间终点（不含）。
        batch_no: 批号，匹配试剂批号或样品批号，精确匹配。
        state: 试验状态。
        operator: 操作人用户名。
    """

    experiment_type: ExperimentType | None = None
    start: datetime | None = None
    end: datetime | None = None
    batch_no: str | None = None
    state: ExperimentState | None = None
    operator: str | None = None


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    """历史列表中的一行试验摘要。

    Attributes:
        experiment_id: 试验标识。
        experiment_type: 试验类型。
        template_id: 模板标识。
        instrument_no: 仪器编号。
        operator: 操作人用户名。
        created_at: 创建时间。
        state: 当前状态。
        tube_count: 试管数。
        signed_by: 签署人。
        signed_at: 签署时间。
    """

    experiment_id: str
    experiment_type: ExperimentType
    template_id: str
    instrument_no: str
    operator: str
    created_at: datetime
    state: ExperimentState
    tube_count: int
    signed_by: str | None = None
    signed_at: datetime | None = None


class MediaKind(StrEnum):
    """媒体文件种类。"""

    VIDEO = "video"
    """视频。"""
    IMAGE = "image"
    """图像。"""


@dataclass(frozen=True, slots=True)
class MediaFile:
    """视频或图像文件记录。数据库只存路径和哈希，不存文件内容。

    Attributes:
        file_id: 文件标识。
        experiment_id: 所属试验。
        kind: 种类。
        path: 文件路径。
        sha256: 文件内容的 SHA-256（64 位小写十六进制）。
        created_at: 记录时间。
        row_index: 排号 1–10，与排无关的文件为 None。
        size_bytes: 文件大小。
    """

    file_id: str
    experiment_id: str
    kind: MediaKind
    path: str
    sha256: str
    created_at: datetime
    row_index: int | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ReportRecord:
    """报告记录。

    Attributes:
        report_id: 报告标识。
        experiment_id: 所属试验。
        path: 报告文件路径。
        sha256: 报告文件的 SHA-256。
        created_by: 生成者用户名。
        created_at: 生成时间。
        signed: 是否已签名。
    """

    report_id: str
    experiment_id: str
    path: str
    sha256: str
    created_by: str
    created_at: datetime
    signed: bool = False


@dataclass(frozen=True, slots=True)
class TemperatureSample:
    """一次温度采样。

    Attributes:
        recorded_at: 采样时间。
        channel: 通道名，例如 ``incubator``。
        celsius: 摄氏度。
        experiment_id: 关联试验，可为空。
    """

    recorded_at: datetime
    channel: str
    celsius: float
    experiment_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceLogEntry:
    """一条设备日志。

    Attributes:
        recorded_at: 记录时间。
        level: 级别，例如 ``INFO``、``WARNING``、``ERROR``。
        source: 来源，例如设备或模块名。
        message: 日志内容。
        experiment_id: 关联试验，可为空。
    """

    recorded_at: datetime
    level: str
    source: str
    message: str
    experiment_id: str | None = None


class UserRepository(Protocol):
    """用户仓储。"""

    def add(self, user: User, *, password_hash: str, must_change_password: bool) -> None:
        """新增用户。

        Args:
            user: 用户。
            password_hash: argon2 密码哈希。
            must_change_password: 首次登录是否必须改密。

        Raises:
            DuplicateError: 用户标识或登录名已存在。
        """
        ...

    def get(self, user_id: str) -> User | None:
        """按标识查询用户。"""
        ...

    def get_by_username(self, username: str) -> User | None:
        """按登录名查询用户。"""
        ...

    def get_credentials(self, username: str) -> UserCredentials | None:
        """按登录名查询登录校验信息。"""
        ...

    def list_all(self) -> list[User]:
        """列出全部用户，按登录名排序。"""
        ...

    def update(self, user: User) -> None:
        """更新显示名称、角色和启用状态。

        Raises:
            NotFoundError: 用户不存在。
        """
        ...

    def set_password_hash(
        self, user_id: str, password_hash: str, *, must_change_password: bool
    ) -> None:
        """更新密码哈希和改密标记。

        Raises:
            NotFoundError: 用户不存在。
        """
        ...


class TemplateRepository(Protocol):
    """试验模板仓储。"""

    def save(self, template: ExperimentTemplate, *, builtin: bool = False) -> None:
        """新增或覆盖模板。

        Args:
            template: 模板。
            builtin: 是否内置模板。
        """
        ...

    def get(self, template_id: str) -> ExperimentTemplate | None:
        """按标识查询模板。"""
        ...

    def list_all(self, experiment_type: ExperimentType | None = None) -> list[ExperimentTemplate]:
        """列出模板，可按试验类型过滤，按标识排序。"""
        ...

    def is_builtin(self, template_id: str) -> bool:
        """模板是否为内置模板，不存在时为 False。"""
        ...


class ExperimentRepository(Protocol):
    """试验仓储。"""

    def save(self, experiment: Experiment) -> None:
        """新增或整体覆盖试验（含管位安排、试剂批次和单管结果）。"""
        ...

    def save_results(self, experiment_id: str, results: Sequence[TubeResult]) -> None:
        """新增或覆盖若干单管结果，供试验引擎按排写入。

        Raises:
            NotFoundError: 试验不存在。
        """
        ...

    def update_state(
        self,
        experiment_id: str,
        state: ExperimentState,
        *,
        expected: ExperimentState | None = None,
    ) -> None:
        """只更新试验状态，不触碰子记录。

        Args:
            experiment_id: 试验标识。
            state: 新状态。
            expected: 预期的当前状态，不为 None 时做比较后更新。

        Raises:
            NotFoundError: 试验不存在。
            StateConflictError: 当前状态与 ``expected`` 不符。
        """
        ...

    def get(self, experiment_id: str) -> Experiment | None:
        """按标识加载完整试验。"""
        ...

    def query(self, query: ExperimentQuery, page: PageRequest) -> Page[ExperimentSummary]:
        """分页查询历史试验，按创建时间倒序。"""
        ...


class AuditRepository(Protocol):
    """审计仓储。只追加，不提供更新和删除。"""

    def append(self, event: AuditEvent) -> AuditRecord:
        """追加一条事件并生成哈希链。"""
        ...

    def append_many(self, events: Sequence[AuditEvent]) -> int:
        """在同一事务中按顺序追加多条事件，返回条数。"""
        ...

    def get(self, seq: int) -> AuditRecord | None:
        """按序号查询一条记录。"""
        ...

    def query(self, query: AuditQuery, page: PageRequest) -> Page[AuditRecord]:
        """分页查询，按序号倒序（最新在前）。"""
        ...

    def verify_chain(self) -> ChainVerification:
        """校验整条哈希链，定位第一条被篡改或删除的记录。"""
        ...


class MediaRepository(Protocol):
    """视频与图像文件记录仓储。"""

    def add(self, media: MediaFile) -> None:
        """新增记录。

        Raises:
            DuplicateError: 文件标识已存在。
            NotFoundError: 试验不存在。
        """
        ...

    def get(self, file_id: str) -> MediaFile | None:
        """按标识查询。"""
        ...

    def list_for_experiment(
        self,
        experiment_id: str,
        *,
        kind: MediaKind | None = None,
        row_index: int | None = None,
    ) -> list[MediaFile]:
        """列出试验的文件，按排号和记录时间排序。"""
        ...


class ReportRepository(Protocol):
    """报告记录仓储。"""

    def add(self, report: ReportRecord) -> None:
        """新增记录。

        Raises:
            DuplicateError: 报告标识已存在。
            NotFoundError: 试验不存在。
        """
        ...

    def get(self, report_id: str) -> ReportRecord | None:
        """按标识查询。"""
        ...

    def list_for_experiment(self, experiment_id: str) -> list[ReportRecord]:
        """列出试验的报告，按生成时间排序。"""
        ...


class TelemetryRepository(Protocol):
    """温度日志与设备日志仓储。"""

    def add_temperatures(self, samples: Sequence[TemperatureSample]) -> int:
        """批量写入温度采样，返回条数。"""
        ...

    def query_temperatures(
        self,
        *,
        experiment_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: PageRequest,
    ) -> Page[TemperatureSample]:
        """分页查询温度采样，按时间正序。"""
        ...

    def add_device_logs(self, entries: Sequence[DeviceLogEntry]) -> int:
        """批量写入设备日志，返回条数。"""
        ...

    def query_device_logs(
        self,
        *,
        experiment_id: str | None = None,
        level: str | None = None,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: PageRequest,
    ) -> Page[DeviceLogEntry]:
        """分页查询设备日志，按时间倒序。"""
        ...


class UnitOfWork(Protocol):
    """一个事务内的仓储集合。通过它写入的内容一起提交或一起回滚。

    从进入事务起就持有数据库写锁，所以应尽快结束，不要在其中调用设备、算法或等待。
    只能在事务内使用；事务内抛出的异常必须传出 ``with`` 块，吞掉后事务不会提交。
    """

    @property
    def experiments(self) -> ExperimentRepository:
        """本事务的试验仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """本事务的审计仓储。"""
        ...

    @property
    def media(self) -> MediaRepository:
        """本事务的媒体文件记录仓储。"""
        ...

    @property
    def telemetry(self) -> TelemetryRepository:
        """本事务的温度与设备日志仓储。"""
        ...


class TransactionalStorage(Protocol):
    """试验引擎需要的存储：读试验，加上开启工作单元。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储，每个方法各自是一个事务。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元：正常退出 ``with`` 块时提交，异常时回滚。

        Returns:
            进入后得到 :class:`UnitOfWork` 的上下文管理器。

        Raises:
            StorageError: 无法取得写锁或提交失败。
        """
        ...


__all__ = [
    "GENESIS_HASH",
    "MAX_PAGE_SIZE",
    "AuditQuery",
    "AuditRecord",
    "AuditRepository",
    "ChainFailure",
    "ChainVerification",
    "DatabaseVersionError",
    "DeviceLogEntry",
    "DuplicateError",
    "ExperimentQuery",
    "ExperimentRepository",
    "ExperimentSummary",
    "MediaFile",
    "MediaKind",
    "MediaRepository",
    "NotFoundError",
    "Page",
    "PageRequest",
    "ReportRecord",
    "ReportRepository",
    "StateConflictError",
    "StorageError",
    "TelemetryRepository",
    "TemperatureSample",
    "TemplateRepository",
    "TransactionalStorage",
    "UnitOfWork",
    "UserCredentials",
    "UserRepository",
]
