"""初始表结构。

Revision ID: 0001
Revises:

迁移脚本是某一时刻的快照，不要导入应用代码（例如自定义列类型），
时间列在这里统一写成 ``String(27)``，与 ``UtcDateTime`` 的存储一致。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到本版本。"""
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_head")),
    )
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("occurred_at", sa.String(length=27), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_events_hash")),
    )
    op.execute(
        "INSERT INTO audit_chain_head (id, last_seq, last_hash) "
        "VALUES (1, 0, '0000000000000000000000000000000000000000000000000000000000000000')"
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index("ix_audit_events_action_seq", ["action", "seq"], unique=False)
        batch_op.create_index("ix_audit_events_actor_seq", ["actor", "seq"], unique=False)
        batch_op.create_index(
            "ix_audit_events_experiment_id_seq", ["experiment_id", "seq"], unique=False
        )
        batch_op.create_index("ix_audit_events_occurred_at", ["occurred_at"], unique=False)

    op.create_table(
        "experiments",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_type", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("instrument_no", sa.String(length=64), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("measured_sensitivity", sa.Float(), nullable=True),
        sa.Column("mvd", sa.Float(), nullable=True),
        sa.Column("endotoxin_limit", sa.Float(), nullable=True),
        sa.Column("signed_by", sa.String(length=64), nullable=True),
        sa.Column("signed_at", sa.String(length=27), nullable=True),
        sa.PrimaryKeyConstraint("experiment_id", name=op.f("pk_experiments")),
    )
    with op.batch_alter_table("experiments", schema=None) as batch_op:
        batch_op.create_index("ix_experiments_created_at", ["created_at"], unique=False)
        batch_op.create_index(
            "ix_experiments_experiment_type_created_at",
            ["experiment_type", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_experiments_operator_created_at", ["operator", "created_at"], unique=False
        )
        batch_op.create_index(
            "ix_experiments_state_created_at", ["state", "created_at"], unique=False
        )

    op.create_table(
        "templates",
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("experiment_type", sa.String(length=32), nullable=False),
        sa.Column("needs_confirmation", sa.Boolean(), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("template_id", name=op.f("pk_templates")),
    )
    with op.batch_alter_table("templates", schema=None) as batch_op:
        batch_op.create_index("ix_templates_experiment_type", ["experiment_type"], unique=False)

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "device_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=True),
        sa.Column("recorded_at", sa.String(length=27), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_device_log_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_log")),
    )
    with op.batch_alter_table("device_log", schema=None) as batch_op:
        batch_op.create_index(
            "ix_device_log_experiment_id_recorded_at",
            ["experiment_id", "recorded_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_device_log_level_recorded_at", ["level", "recorded_at"], unique=False
        )
        batch_op.create_index("ix_device_log_recorded_at", ["recorded_at"], unique=False)

    op.create_table(
        "media_files",
        sa.Column("file_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_media_files_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("file_id", name=op.f("pk_media_files")),
    )
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.create_index(
            "ix_media_files_experiment_id_row_index", ["experiment_id", "row_index"], unique=False
        )
        batch_op.create_index("ix_media_files_sha256", ["sha256"], unique=False)

    op.create_table(
        "reagent_batches",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("batch_no", sa.String(length=128), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False),
        sa.Column("labeled_sensitivity", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_reagent_batches_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "experiment_id", "kind", "batch_no", name=op.f("pk_reagent_batches")
        ),
    )
    with op.batch_alter_table("reagent_batches", schema=None) as batch_op:
        batch_op.create_index("ix_reagent_batches_batch_no", ["batch_no"], unique=False)

    op.create_table(
        "reports",
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("signed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_reports_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("report_id", name=op.f("pk_reports")),
    )
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.create_index("ix_reports_experiment_id", ["experiment_id"], unique=False)

    op.create_table(
        "temperature_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=True),
        sa.Column("recorded_at", sa.String(length=27), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("celsius", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_temperature_log_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temperature_log")),
    )
    with op.batch_alter_table("temperature_log", schema=None) as batch_op:
        batch_op.create_index(
            "ix_temperature_log_experiment_id_recorded_at",
            ["experiment_id", "recorded_at"],
            unique=False,
        )
        batch_op.create_index("ix_temperature_log_recorded_at", ["recorded_at"], unique=False)

    op.create_table(
        "template_items",
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("tube_no", sa.Integer(), nullable=False),
        sa.Column("sample_name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("dilution_factor", sa.Float(), nullable=True),
        sa.Column("spiked_endotoxin", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["templates.template_id"],
            name=op.f("fk_template_items_template_id_templates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("template_id", "tube_no", name=op.f("pk_template_items")),
    )
    op.create_table(
        "tube_assignments",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("tube_no", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.String(length=64), nullable=False),
        sa.Column("sample_name", sa.String(length=128), nullable=False),
        sa.Column("sample_serial_no", sa.String(length=128), nullable=True),
        sa.Column("sample_batch_no", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("dilution_factor", sa.Float(), nullable=True),
        sa.Column("spiked_endotoxin", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_tube_assignments_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("experiment_id", "tube_no", name=op.f("pk_tube_assignments")),
    )
    with op.batch_alter_table("tube_assignments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tube_assignments_sample_batch_no", ["sample_batch_no"], unique=False
        )

    op.create_table(
        "tube_results",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("tube_no", sa.Integer(), nullable=False),
        sa.Column("analysis_status", sa.String(length=32), nullable=False),
        sa.Column("gel_state", sa.String(length=32), nullable=True),
        sa.Column("auto_interpretation", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("config_version", sa.String(length=128), nullable=True),
        sa.Column("elapsed_ms", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("reviewed_interpretation", sa.String(length=32), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.String(length=27), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_tube_results_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("experiment_id", "tube_no", name=op.f("pk_tube_results")),
    )
    with op.batch_alter_table("tube_results", schema=None) as batch_op:
        batch_op.create_index("ix_tube_results_model_version", ["model_version"], unique=False)
        batch_op.create_index("ix_tube_results_review_status", ["review_status"], unique=False)


def downgrade() -> None:
    """回退到上一版本。"""
    with op.batch_alter_table("tube_results", schema=None) as batch_op:
        batch_op.drop_index("ix_tube_results_review_status")
        batch_op.drop_index("ix_tube_results_model_version")

    op.drop_table("tube_results")
    with op.batch_alter_table("tube_assignments", schema=None) as batch_op:
        batch_op.drop_index("ix_tube_assignments_sample_batch_no")

    op.drop_table("tube_assignments")
    op.drop_table("template_items")
    with op.batch_alter_table("temperature_log", schema=None) as batch_op:
        batch_op.drop_index("ix_temperature_log_recorded_at")
        batch_op.drop_index("ix_temperature_log_experiment_id_recorded_at")

    op.drop_table("temperature_log")
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_index("ix_reports_experiment_id")

    op.drop_table("reports")
    with op.batch_alter_table("reagent_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_reagent_batches_batch_no")

    op.drop_table("reagent_batches")
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.drop_index("ix_media_files_sha256")
        batch_op.drop_index("ix_media_files_experiment_id_row_index")

    op.drop_table("media_files")
    with op.batch_alter_table("device_log", schema=None) as batch_op:
        batch_op.drop_index("ix_device_log_recorded_at")
        batch_op.drop_index("ix_device_log_level_recorded_at")
        batch_op.drop_index("ix_device_log_experiment_id_recorded_at")

    op.drop_table("device_log")
    op.drop_table("users")
    with op.batch_alter_table("templates", schema=None) as batch_op:
        batch_op.drop_index("ix_templates_experiment_type")

    op.drop_table("templates")
    with op.batch_alter_table("experiments", schema=None) as batch_op:
        batch_op.drop_index("ix_experiments_state_created_at")
        batch_op.drop_index("ix_experiments_operator_created_at")
        batch_op.drop_index("ix_experiments_experiment_type_created_at")
        batch_op.drop_index("ix_experiments_created_at")

    op.drop_table("experiments")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_events_occurred_at")
        batch_op.drop_index("ix_audit_events_experiment_id_seq")
        batch_op.drop_index("ix_audit_events_actor_seq")
        batch_op.drop_index("ix_audit_events_action_seq")

    op.drop_table("audit_events")
    op.drop_table("audit_chain_head")
