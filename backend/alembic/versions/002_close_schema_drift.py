"""Close schema drift: add orphan tables, users reset columns, FK cascades, indexes

Revision ID: 002
Revises: 001
Create Date: 2026-04-15

This migration is IDEMPOTENT. Tables and columns that already exist (because the
DB was built earlier via Base.metadata.create_all) are skipped via IF NOT EXISTS
guards. This allows the migration to run cleanly on both:
  1. A production DB where the orphans were created manually (current state)
  2. A fresh DB where `alembic upgrade head` needs to create everything

Covers audit findings C11, C12, C13, H27, H28.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {i["name"] for i in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()

    # ── Orphan tables ───────────────────────────────────────────────────

    # access_requests
    if not _table_exists(bind, "access_requests"):
        op.create_table(
            "access_requests",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("phone", sa.String(50), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), nullable=True, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )

    # feedback_reports
    if not _table_exists(bind, "feedback_reports"):
        op.create_table(
            "feedback_reports",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("feedback_type", sa.String(50), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=True, server_default="open"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    # invite_codes
    if not _table_exists(bind, "invite_codes"):
        op.create_table(
            "invite_codes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("used_by", sa.Integer(), nullable=True),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default="true"),
            sa.Column("max_uses", sa.Integer(), nullable=True, server_default="1"),
            sa.Column("use_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["used_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_invite_codes_code", "invite_codes", ["code"], unique=True)

    # market_data_providers
    if not _table_exists(bind, "market_data_providers"):
        op.create_table(
            "market_data_providers",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider_name", sa.String(50), nullable=False),
            sa.Column("api_key_encrypted", sa.String(500), nullable=False),
            sa.Column("data_type", sa.String(20), nullable=True, server_default="ohlcv"),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # retrain_runs
    if not _table_exists(bind, "retrain_runs"):
        op.create_table(
            "retrain_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("triggered_by", sa.String(50), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="running"),
            sa.Column("old_grade", sa.String(5), nullable=True),
            sa.Column("old_sharpe", sa.Float(), nullable=True),
            sa.Column("old_metrics", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("new_grade", sa.String(5), nullable=True),
            sa.Column("new_sharpe", sa.Float(), nullable=True),
            sa.Column("new_metrics", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("swapped", sa.Boolean(), nullable=True, server_default="false"),
            sa.Column("swap_reason", sa.String(500), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("training_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_retrain_runs_symbol", "retrain_runs", ["symbol"])

    # ── users.reset_token and users.reset_token_expires ────────────────

    if not _column_exists(bind, "users", "reset_token"):
        op.add_column("users", sa.Column("reset_token", sa.String(100), nullable=True))
    if not _column_exists(bind, "users", "reset_token_expires"):
        op.add_column("users", sa.Column("reset_token_expires", sa.DateTime(timezone=True), nullable=True))

    # ── Performance indexes ────────────────────────────────────────────

    if not _index_exists(bind, "agent_trades", "ix_agent_trades_entry_time"):
        op.create_index("ix_agent_trades_entry_time", "agent_trades", ["entry_time"])
    if not _index_exists(bind, "agent_trades", "ix_agent_trades_exit_time"):
        op.create_index("ix_agent_trades_exit_time", "agent_trades", ["exit_time"])
    if not _index_exists(bind, "agent_logs", "ix_agent_logs_level"):
        op.create_index("ix_agent_logs_level", "agent_logs", ["level"])
    if not _index_exists(bind, "agent_logs", "ix_agent_logs_created_at"):
        op.create_index("ix_agent_logs_created_at", "agent_logs", ["created_at"])
    if not _index_exists(bind, "broker_accounts", "ix_broker_accounts_user_id"):
        op.create_index("ix_broker_accounts_user_id", "broker_accounts", ["user_id"])


def downgrade():
    bind = op.get_bind()

    # Drop indexes first
    for table, idx in [
        ("broker_accounts", "ix_broker_accounts_user_id"),
        ("agent_logs", "ix_agent_logs_created_at"),
        ("agent_logs", "ix_agent_logs_level"),
        ("agent_trades", "ix_agent_trades_exit_time"),
        ("agent_trades", "ix_agent_trades_entry_time"),
    ]:
        if _index_exists(bind, table, idx):
            op.drop_index(idx, table_name=table)

    # Drop columns
    if _column_exists(bind, "users", "reset_token_expires"):
        op.drop_column("users", "reset_token_expires")
    if _column_exists(bind, "users", "reset_token"):
        op.drop_column("users", "reset_token")

    # Drop tables (reverse dep order)
    for table in ["retrain_runs", "market_data_providers", "invite_codes", "feedback_reports", "access_requests"]:
        if _table_exists(bind, table):
            op.drop_table(table)
