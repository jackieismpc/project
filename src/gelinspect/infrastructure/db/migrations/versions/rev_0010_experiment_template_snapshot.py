"""保存创建试验时实际使用的模板配置快照。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为历史试验添加可空的模板快照字段。"""
    op.add_column("experiments", sa.Column("template_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    """移除试验模板快照字段。"""
    op.drop_column("experiments", "template_snapshot")
