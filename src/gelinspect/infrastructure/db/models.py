"""SQLAlchemy ORM 模型。

ORM 类只在 ``infrastructure.db`` 内部使用，对外通过仓储与 domain 实体转换，
不会泄漏到 domain、services 或 ui。枚举以其字符串值存储，不使用数据库枚举类型，
这样新增枚举值不需要迁移。

表中没有任何「是否模拟」字段：模拟与真实数据不做区分（``docs/PLAN.md`` 第 3 节）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gelinspect.infrastructure.db.columns import UtcDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""约束命名约定，保证迁移脚本里的名字稳定。"""


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRow(Base):
    """用户表。密码哈希只存在这里。"""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class TemplateRow(Base):
    """试验模板表。"""

    __tablename__ = "templates"
    __table_args__ = (Index("ix_templates_experiment_type", "experiment_type"),)

    template_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    experiment_type: Mapped[str] = mapped_column(String(32))
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class TemplateItemRow(Base):
    """模板管位项表。"""

    __tablename__ = "template_items"

    template_id: Mapped[str] = mapped_column(
        ForeignKey("templates.template_id", ondelete="CASCADE"), primary_key=True
    )
    tube_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    dilution_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    spiked_endotoxin: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExperimentRow(Base):
    """试验表。``template_id`` 和 ``operator`` 是历史快照，不设外键。"""

    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_created_at", "created_at"),
        Index("ix_experiments_experiment_type_created_at", "experiment_type", "created_at"),
        Index("ix_experiments_state_created_at", "state", "created_at"),
        Index("ix_experiments_operator_created_at", "operator", "created_at"),
    )

    experiment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_type: Mapped[str] = mapped_column(String(32))
    template_id: Mapped[str] = mapped_column(String(128))
    instrument_no: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    state: Mapped[str] = mapped_column(String(32))
    measured_sensitivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    mvd: Mapped[float | None] = mapped_column(Float, nullable=True)
    endotoxin_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class TubeAssignmentRow(Base):
    """管位分配与样品表。样品信息直接内嵌，随试验保存快照。"""

    __tablename__ = "tube_assignments"
    __table_args__ = (Index("ix_tube_assignments_sample_batch_no", "sample_batch_no"),)

    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"), primary_key=True
    )
    tube_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(64))
    sample_name: Mapped[str] = mapped_column(String(128))
    sample_serial_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sample_batch_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    dilution_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    spiked_endotoxin: Mapped[float | None] = mapped_column(Float, nullable=True)


class ReagentBatchRow(Base):
    """试剂批次表，记录试验使用的批次快照。``position`` 保留录入顺序。"""

    __tablename__ = "reagent_batches"
    __table_args__ = (Index("ix_reagent_batches_batch_no", "batch_no"),)

    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(128), primary_key=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    registered: Mapped[bool] = mapped_column(Boolean)
    labeled_sensitivity: Mapped[float | None] = mapped_column(Float, nullable=True)


class TubeResultRow(Base):
    """单管结果表。``auto_interpretation`` 等是阴阳性与复核信息。"""

    __tablename__ = "tube_results"
    __table_args__ = (
        Index("ix_tube_results_review_status", "review_status"),
        Index("ix_tube_results_model_version", "model_version"),
    )

    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"), primary_key=True
    )
    tube_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_status: Mapped[str] = mapped_column(String(32))
    gel_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auto_interpretation: Mapped[str] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_interpretation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class MediaFileRow(Base):
    """视频与图像文件记录表。"""

    __tablename__ = "media_files"
    __table_args__ = (
        Index("ix_media_files_experiment_id_row_index", "experiment_id", "row_index"),
        Index("ix_media_files_sha256", "sha256"),
    )

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ReportRow(Base):
    """报告记录表。"""

    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_experiment_id", "experiment_id"),)

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    signed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEventRow(Base):
    """审计事件表。只追加；``hash`` 与 ``prev_hash`` 构成哈希链。"""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_actor_seq", "actor", "seq"),
        Index("ix_audit_events_action_seq", "action", "seq"),
        Index("ix_audit_events_experiment_id_seq", "experiment_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    actor: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(48))
    detail: Mapped[str] = mapped_column(Text)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditChainHeadRow(Base):
    """审计链头，只有一行（``id=1``），记录最后一条的序号和哈希。

    用来发现尾部记录被删除：只靠记录之间的链接，删除最后几条无法察觉。
    """

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_seq: Mapped[int] = mapped_column(Integer)
    last_hash: Mapped[str] = mapped_column(String(64))


class TemperatureLogRow(Base):
    """温度日志表。"""

    __tablename__ = "temperature_log"
    __table_args__ = (
        Index("ix_temperature_log_recorded_at", "recorded_at"),
        Index("ix_temperature_log_experiment_id_recorded_at", "experiment_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    channel: Mapped[str] = mapped_column(String(64))
    celsius: Mapped[float] = mapped_column(Float)


class DeviceLogRow(Base):
    """设备日志表。"""

    __tablename__ = "device_log"
    __table_args__ = (
        Index("ix_device_log_recorded_at", "recorded_at"),
        Index("ix_device_log_experiment_id_recorded_at", "experiment_id", "recorded_at"),
        Index("ix_device_log_level_recorded_at", "level", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    level: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
