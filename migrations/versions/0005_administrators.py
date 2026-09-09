"""Separate administrator accounts, sessions, recovery, and throttling."""
from alembic import op
import sqlalchemy as sa

revision = "0005_administrators"
down_revision = "0004_rive_character"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("administrators",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("must_change_password", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin')", name="administrator_role"))
    for table in ["administrator_sessions", "administrator_resets"]:
        op.create_table(table,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("administrator_id", sa.String(36), sa.ForeignKey("administrators.id"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at" if table.endswith("sessions") else "used_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index(f"ix_{table}_administrator_id", table, ["administrator_id"])
    op.create_index("ix_administrator_sessions_expires_at", "administrator_sessions", ["expires_at"])
    op.create_table("administrator_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("failed", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for name in ["kind", "email_hash", "created_at"]:
        op.create_index(f"ix_administrator_attempts_{name}", "administrator_attempts", [name])
    op.create_table("administrator_lock", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("revision", sa.Integer, nullable=False))
    op.bulk_insert(sa.table("administrator_lock", sa.column("id", sa.Integer), sa.column("revision", sa.Integer)),
                   [{"id": 1, "revision": 0}])


def downgrade():
    for table in ["administrator_lock", "administrator_attempts", "administrator_resets", "administrator_sessions", "administrators"]:
        op.drop_table(table)
