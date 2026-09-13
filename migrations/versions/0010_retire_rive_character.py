"""Retire the Rive character flight while preserving other experiments."""
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "0010_retire_rive_character"
down_revision = "0009_telemetry_reporting"
branch_labels = None
depends_on = None


def rules_table():
    return sa.table("experiment_rules", sa.column("key", sa.String),
                    sa.column("enabled", sa.Boolean), sa.column("rollout_percentage", sa.Integer),
                    sa.column("allocation_salt", sa.String), sa.column("updated_at", sa.DateTime(timezone=True)))


def upgrade():
    rules = rules_table()
    op.execute(rules.delete().where(rules.c.key == "rive_character"))


def downgrade():
    rules = rules_table()
    op.bulk_insert(rules, [{"key": "rive_character", "enabled": False, "rollout_percentage": 0,
                           "allocation_salt": "rive-character-v1", "updated_at": datetime.now(UTC)}])
