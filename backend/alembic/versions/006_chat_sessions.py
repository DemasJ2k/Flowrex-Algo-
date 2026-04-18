"""Add chat_sessions and chat_messages tables

Revision ID: 006
Revises: 005
Create Date: 2026-04-16

Persistent AI chat: messages survive restarts, multiple sessions per user.
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name):
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, "chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(200), server_default="New Chat"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    if not _table_exists(bind, "chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("model", sa.String(50), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade():
    bind = op.get_bind()
    if _table_exists(bind, "chat_messages"):
        op.drop_table("chat_messages")
    if _table_exists(bind, "chat_sessions"):
        op.drop_table("chat_sessions")
