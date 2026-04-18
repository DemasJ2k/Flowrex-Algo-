"""Add telegram_bindings table

Revision ID: 008
Revises: 007
Create Date: 2026-04-17

Short-lived binding codes: user requests /connect, backend generates 6-char code,
user sends "/start <code>" to @FlowrexAlgoBot, webhook binds chat_id to user.
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name):
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    if not _table_exists(bind, "telegram_bindings"):
        op.create_table(
            "telegram_bindings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(20), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_telegram_bindings_code", "telegram_bindings", ["code"])
        op.create_index("ix_telegram_bindings_user_id", "telegram_bindings", ["user_id"])


def downgrade():
    bind = op.get_bind()
    if _table_exists(bind, "telegram_bindings"):
        op.drop_table("telegram_bindings")
