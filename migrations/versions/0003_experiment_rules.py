"""Database-managed experiment rollout rules."""
from datetime import UTC, datetime
from alembic import op
import sqlalchemy as sa

revision = "0003_experiment_rules"
down_revision = "0002_billing_freshness"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "experiment_rules",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False),
        sa.Column("allocation_salt", sa.String(80), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="experiment_percentage_range"),
    )
    op.bulk_insert(table, [{"key": "companion", "enabled": True, "rollout_percentage": 100,
                           "allocation_salt": "companion-v1", "updated_at": datetime.now(UTC)}])


def downgrade():
    op.drop_table("experiment_rules")
