"""模板隐藏与软删除状态。

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可逆的内置隐藏及自建删除标记。"""
    op.add_column(
        "templates", sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("templates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("templates", sa.Column("deleted_by", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """移除模板生命周期状态。"""
    op.drop_column("templates", "deleted_by")
    op.drop_column("templates", "deleted_at")
    op.drop_column("templates", "hidden")
