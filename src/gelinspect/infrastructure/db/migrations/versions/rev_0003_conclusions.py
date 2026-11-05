"""增加试验结论、完成时间、模板 λ 倍数和抓帧时间。

Revision ID: 0003
Revises: 0002

迁移脚本是某一时刻的快照，不要导入应用代码，时间列写成 ``String(27)``。

数据迁移：

* 内置灵敏度模板从绝对值（1、0.5、0.25、0.125、0）改成 λ 倍数（2、1、0.5、0.25、0）。
  自定义模板不改：无法判断它们的数值是不是按 λ=0.5 写的，保留绝对值，
  结论规则会核对它们与 λ 是否相符。
* 已完成和已签署的试验，完成时间取审计里最后一次转入 ``completed`` 的时间，没有记录的留空。
* 试验的 ``layout_confirmed`` 按模板当前的 ``needs_confirmation`` 回填，模板已删除的按未确认处理。
* 不补算旧试验的结论：旧试验的外加内毒素可能与 λ 不符，界面说明没有入库的结论。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUILTIN_SENSITIVITY = "builtin.sensitivity_confirmation_2lambda_1"


def upgrade() -> None:
    """升级到本版本。"""
    with op.batch_alter_table("template_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lambda_multiple", sa.Float(), nullable=True))
    with op.batch_alter_table("experiments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("completed_at", sa.String(length=27), nullable=True))
        batch_op.add_column(
            sa.Column("layout_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("captured_at", sa.String(length=27), nullable=True))
        batch_op.add_column(sa.Column("flip_angle_deg", sa.Float(), nullable=True))
    op.create_table(
        "experiment_conclusions",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("labeled_sensitivity", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.String(length=27), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name=op.f("fk_experiment_conclusions_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("experiment_id", name=op.f("pk_experiment_conclusions")),
    )
    op.execute(
        sa.text(
            "UPDATE template_items SET "
            "lambda_multiple = CASE "
            "WHEN spiked_endotoxin = 1.0 THEN 2.0 "
            "WHEN spiked_endotoxin = 0.5 THEN 1.0 "
            "WHEN spiked_endotoxin = 0.25 THEN 0.5 "
            "WHEN spiked_endotoxin = 0.125 THEN 0.25 "
            "WHEN spiked_endotoxin = 0.0 THEN 0.0 END, "
            "spiked_endotoxin = NULL "
            "WHERE template_id = :template_id"
        ).bindparams(template_id=_BUILTIN_SENSITIVITY)
    )
    op.execute(
        sa.text(
            "UPDATE experiments SET completed_at = ("
            "SELECT MAX(a.occurred_at) FROM audit_events a "
            "WHERE a.experiment_id = experiments.experiment_id "
            "AND a.action = 'experiment_state_changed' "
            "AND a.detail LIKE '% → completed（%') "
            "WHERE state IN ('completed', 'signed')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE experiments SET layout_confirmed = COALESCE(("
            "SELECT CASE WHEN t.needs_confirmation THEN 0 ELSE 1 END FROM templates t "
            "WHERE t.template_id = experiments.template_id), 0)"
        )
    )


def downgrade() -> None:
    """回退到上一版本。内置灵敏度模板恢复成 λ=0.5 时的绝对值。"""
    op.execute(
        sa.text(
            "UPDATE template_items SET spiked_endotoxin = lambda_multiple * 0.5 "
            "WHERE template_id = :template_id"
        ).bindparams(template_id=_BUILTIN_SENSITIVITY)
    )
    op.drop_table("experiment_conclusions")
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.drop_column("flip_angle_deg")
        batch_op.drop_column("captured_at")
    with op.batch_alter_table("experiments", schema=None) as batch_op:
        batch_op.drop_column("layout_confirmed")
        batch_op.drop_column("completed_at")
    with op.batch_alter_table("template_items", schema=None) as batch_op:
        batch_op.drop_column("lambda_multiple")
