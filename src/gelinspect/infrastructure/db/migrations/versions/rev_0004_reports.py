"""报告记录增加范围、批号、语言、签署信息和证书指纹，试验标识可为空。

Revision ID: 0004
Revises: 0003

迁移脚本是某一时刻的快照，不要导入应用代码，时间列写成 ``String(27)``。

单批次报告汇总多个试验，所以 ``reports.experiment_id`` 改为可空，成员试验放在 ``experiment_ids``
（JSON 数组）。已有的报告记录（P11 之前没有任何入口生成报告）按单个试验的未签署报告处理。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到本版本。"""
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.alter_column("experiment_id", existing_type=sa.String(length=64), nullable=True)
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'experiment'"),
            )
        )
        batch_op.add_column(sa.Column("batch_kind", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("batch_no", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "language", sa.String(length=8), nullable=False, server_default=sa.text("'zh_CN'")
            )
        )
        batch_op.add_column(sa.Column("signed_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signed_at", sa.String(length=27), nullable=True))
        batch_op.add_column(sa.Column("certificate_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("content_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("experiment_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.create_index("ix_reports_batch_no", ["batch_no"], unique=False)


def downgrade() -> None:
    """回退到上一版本。单批次报告（没有试验标识）会被删除。"""
    op.execute(sa.text("DELETE FROM reports WHERE experiment_id IS NULL"))
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_index("ix_reports_batch_no")
        batch_op.drop_column("experiment_ids")
        batch_op.drop_column("content_sha256")
        batch_op.drop_column("certificate_sha256")
        batch_op.drop_column("signed_at")
        batch_op.drop_column("signed_by")
        batch_op.drop_column("language")
        batch_op.drop_column("batch_no")
        batch_op.drop_column("batch_kind")
        batch_op.drop_column("scope")
        batch_op.alter_column("experiment_id", existing_type=sa.String(length=64), nullable=False)
