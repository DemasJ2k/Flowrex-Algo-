"""Add backtest_results table

Revision ID: 005
Revises: 004
Create Date: 2026-04-16

The BacktestResult model existed since early phases but was never migrated.
The backtest page was completely broken — "Internal server error" on every Run.
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name):
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    if not _table_exists(bind, "backtest_results"):
        op.create_table(
            "backtest_results",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("agent_type", sa.String(20), nullable=True, server_default="potential"),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("results", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=True, server_default="running"),
            sa.Column("error_message", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_backtest_results_user_id", "backtest_results", ["user_id"])


def downgrade():
    bind = op.get_bind()
    if _table_exists(bind, "backtest_results"):
        op.drop_table("backtest_results")
