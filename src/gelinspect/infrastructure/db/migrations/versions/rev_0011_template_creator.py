"""记录自定义模板创建人和创建时间。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增可空创建元数据，不推测或回填既有模板。"""
    op.add_column("templates", sa.Column("created_by", sa.String(length=64), nullable=True))
    op.add_column("templates", sa.Column("created_at", sa.String(length=27), nullable=True))


def downgrade() -> None:
    """移除模板创建元数据。"""
    op.drop_column("templates", "created_at")
    op.drop_column("templates", "created_by")
