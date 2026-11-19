"""保留历史校验的试剂批次列表移除水位。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建提醒关闭水位表。"""
    op.create_table(
        "reagent_attention_dismissals",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("batch_no", sa.String(128), primary_key=True),
        sa.Column("through_id", sa.Integer(), nullable=False),
    )
    op.create_table(
        "media_purge_records",
        sa.Column("file_id", sa.String(64), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
    )


def downgrade() -> None:
    """移除提醒关闭水位表。"""
    op.drop_table("media_purge_records")
    op.drop_table("reagent_attention_dismissals")
