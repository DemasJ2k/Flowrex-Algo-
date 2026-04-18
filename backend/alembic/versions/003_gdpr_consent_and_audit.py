"""GDPR consent columns on users, admin audit log table, age verification

Revision ID: 003
Revises: 002
Create Date: 2026-04-16

Adds:
  - users.terms_accepted_at, terms_version, privacy_accepted_at, date_of_birth
  - admin_audit_logs table (tracks admin data access for GDPR Art. 32)

Idempotent: checks for existence before creating.
"""
from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()

    # ── GDPR consent columns on users ──────────────────────────────────
    if not _column_exists(bind, "users", "terms_accepted_at"):
        op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    if not _column_exists(bind, "users", "terms_version"):
        op.add_column("users", sa.Column("terms_version", sa.String(20), nullable=True))
    if not _column_exists(bind, "users", "privacy_accepted_at"):
        op.add_column("users", sa.Column("privacy_accepted_at", sa.DateTime(timezone=True), nullable=True))
    if not _column_exists(bind, "users", "date_of_birth"):
        op.add_column("users", sa.Column("date_of_birth", sa.Date(), nullable=True))

    # ── Admin audit log ────────────────────────────────────────────────
    if not _table_exists(bind, "admin_audit_logs"):
        op.create_table(
            "admin_audit_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("admin_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=True),
            sa.Column("resource_id", sa.Integer(), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_admin_audit_logs_admin_id", "admin_audit_logs", ["admin_id"])
        op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])


def downgrade():
    bind = op.get_bind()

    if _table_exists(bind, "admin_audit_logs"):
        op.drop_table("admin_audit_logs")

    for col in ["date_of_birth", "privacy_accepted_at", "terms_version", "terms_accepted_at"]:
        if _column_exists(bind, "users", col):
            op.drop_column("users", col)
