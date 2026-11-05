"""增加试剂校验记录表。

Revision ID: 0002
Revises: 0001

迁移脚本是某一时刻的快照，不要导入应用代码，时间列写成 ``String(27)``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到本版本。"""
    op.create_table(
        "reagent_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("checked_at", sa.String(length=27), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("batch_no", sa.String(length=128), nullable=False),
        sa.Column("product_code", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("labeled_sensitivity", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_code", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reagent_checks")),
    )
    with op.batch_alter_table("reagent_checks", schema=None) as batch_op:
        batch_op.create_index("ix_reagent_checks_checked_at", ["checked_at"], unique=False)
        batch_op.create_index("ix_reagent_checks_kind_batch_no", ["kind", "batch_no"], unique=False)


def downgrade() -> None:
    """回退到上一版本。"""
    with op.batch_alter_table("reagent_checks", schema=None) as batch_op:
        batch_op.drop_index("ix_reagent_checks_kind_batch_no")
        batch_op.drop_index("ix_reagent_checks_checked_at")
    op.drop_table("reagent_checks")
