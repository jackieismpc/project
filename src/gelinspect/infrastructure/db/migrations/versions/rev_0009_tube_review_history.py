"""保留单管复核历史。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """添加单管人工复核历史表。"""
    op.add_column(
        "tube_results",
        sa.Column("review_history", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "reports",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "reports", sa.Column("supersedes_report_id", sa.String(length=64), nullable=True)
    )
    # Preserve the latest review already present before the history field existed.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE tube_results SET review_history = json_array(json_object("
            "'interpretation', reviewed_interpretation, 'reviewed_by', reviewed_by, "
            "'reviewed_at', reviewed_at, 'reason', review_note)) "
            "WHERE reviewed_interpretation IS NOT NULL"
        )
    )


def downgrade() -> None:
    """移除单管人工复核历史表。"""
    op.drop_column("reports", "supersedes_report_id")
    op.drop_column("reports", "revision")
    op.drop_column("tube_results", "review_history")
