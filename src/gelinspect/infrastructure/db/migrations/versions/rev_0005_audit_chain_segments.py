"""审计链分段：记录从哪个序号起改用 HMAC-SHA256 以及所用密钥的标识。

Revision ID: 0005
Revises: 0004

迁移脚本是某一时刻的快照，不要导入应用代码。

旧记录不改动，仍按 SHA-256 校验；第一次以带密钥的方式启动时追加一条切换事件，
从它开始的记录用 HMAC-SHA256 计算。本表只是数据库里的副本，校验时以密钥文件里
（受系统凭据保护）记录的切换点为准，两者不一致即视为篡改。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到本版本。"""
    op.create_table(
        "audit_chain_segments",
        sa.Column("from_seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("from_seq", name=op.f("pk_audit_chain_segments")),
    )


def downgrade() -> None:
    """回退到上一版本。已用 HMAC 计算的记录在旧版本里会校验失败。"""
    op.drop_table("audit_chain_segments")
