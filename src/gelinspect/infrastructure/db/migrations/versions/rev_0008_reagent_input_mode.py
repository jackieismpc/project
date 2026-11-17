"""记录试剂输入方式。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加输入方式字段，旧记录标记为扫码。"""
    # SQLite cannot drop a column default with ALTER COLUMN; keep the harmless default.
    op.add_column(
        "reagent_checks",
        sa.Column("input_mode", sa.String(16), nullable=False, server_default="scan"),
    )


def downgrade() -> None:
    """移除输入方式字段。"""
    op.drop_column("reagent_checks", "input_mode")
