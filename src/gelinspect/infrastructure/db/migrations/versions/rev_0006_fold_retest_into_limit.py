"""复试并入限度试验：删除“复试试验”类型。

Revision ID: 0006
Revises: 0005

迁移脚本是某一时刻的快照，不要导入应用代码。

中国药典通则 1143 的复试是限度试验的第二步，不是单独的试验类型。本迁移：

* 删除旧的内置复试模板 ``builtin.retest``
  （启动时会写入新的 ``builtin.limit_retest``，类型为限度试验）；
* 自定义模板和已有试验中的 ``retest`` 类型改为 ``limit``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到本版本。"""
    op.execute(sa.text("DELETE FROM template_items WHERE template_id = 'builtin.retest'"))
    op.execute(sa.text("DELETE FROM templates WHERE template_id = 'builtin.retest'"))
    op.execute(
        sa.text("UPDATE templates SET experiment_type = 'limit' WHERE experiment_type = 'retest'")
    )
    op.execute(
        sa.text("UPDATE experiments SET experiment_type = 'limit' WHERE experiment_type = 'retest'")
    )


def downgrade() -> None:
    """回退到上一版本：数据改动不可逆，只删除新的复试模板，旧版本启动时会重建它的复试模板。"""
    op.execute(sa.text("DELETE FROM template_items WHERE template_id = 'builtin.limit_retest'"))
    op.execute(sa.text("DELETE FROM templates WHERE template_id = 'builtin.limit_retest'"))
