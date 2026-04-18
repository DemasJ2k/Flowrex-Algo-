"""Add execution quality tracking columns to agent_trades

Revision ID: 004
Revises: 003
Create Date: 2026-04-16

Adds requested_price, fill_price, slippage_pips for trade execution quality analysis.
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name, column_name):
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    if not _column_exists(bind, "agent_trades", "requested_price"):
        op.add_column("agent_trades", sa.Column("requested_price", sa.Float(), nullable=True))
    if not _column_exists(bind, "agent_trades", "fill_price"):
        op.add_column("agent_trades", sa.Column("fill_price", sa.Float(), nullable=True))
    if not _column_exists(bind, "agent_trades", "slippage_pips"):
        op.add_column("agent_trades", sa.Column("slippage_pips", sa.Float(), nullable=True))


def downgrade():
    bind = op.get_bind()
    for col in ["slippage_pips", "fill_price", "requested_price"]:
        if _column_exists(bind, "agent_trades", col):
            op.drop_column("agent_trades", col)
