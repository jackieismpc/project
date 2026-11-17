"""报告记录增加语言、签署信息和证书指纹。

Revision ID: 0004
Revises: 0003

迁移脚本是某一时刻的快照，不要导入应用代码，时间列写成 ``String(27)``。

报告按独立实验记录。开发期结构与当前模型同步，不承诺旧开发数据兼容。
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
        batch_op.add_column(
            sa.Column(
                "language", sa.String(length=8), nullable=False, server_default=sa.text("'zh_CN'")
            )
        )
        batch_op.add_column(sa.Column("signed_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signed_at", sa.String(length=27), nullable=True))
        batch_op.add_column(sa.Column("certificate_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("content_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """回退到上一版本。没有实验标识的记录会被删除。"""
    op.execute(sa.text("DELETE FROM reports WHERE experiment_id IS NULL"))
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_column("content_sha256")
        batch_op.drop_column("certificate_sha256")
        batch_op.drop_column("signed_at")
        batch_op.drop_column("signed_by")
        batch_op.drop_column("language")
        batch_op.drop_column("scope")
        batch_op.alter_column("experiment_id", existing_type=sa.String(length=64), nullable=False)
