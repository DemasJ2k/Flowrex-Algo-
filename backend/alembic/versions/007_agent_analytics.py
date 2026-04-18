"""Add analytics columns to agent_trades

Revision ID: 007
Revises: 006
Create Date: 2026-04-16

Enriched trade data for analytics: session, MTF, SHAP features, timing.
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def _col_exists(bind, table, column):
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns(table)]
    return column in cols


def upgrade():
    bind = op.get_bind()
    new_cols = [
        ("mtf_score", sa.Integer(), None),
        ("mtf_layers", sa.JSON(), None),
        ("session_name", sa.String(20), None),
        ("top_features", sa.JSON(), None),
        ("atr_at_entry", sa.Float(), None),
        ("model_name", sa.String(50), None),
        ("time_to_exit_seconds", sa.Integer(), None),
        ("bars_to_exit", sa.Integer(), None),
    ]
    for col_name, col_type, default in new_cols:
        if not _col_exists(bind, "agent_trades", col_name):
            op.add_column("agent_trades", sa.Column(col_name, col_type, nullable=True))


def downgrade():
    for col_name in [
        "mtf_score", "mtf_layers", "session_name", "top_features",
        "atr_at_entry", "model_name", "time_to_exit_seconds", "bars_to_exit",
    ]:
        op.drop_column("agent_trades", col_name)
