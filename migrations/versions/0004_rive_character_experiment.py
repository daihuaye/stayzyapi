"""Register the Rive character rollout, disabled by default."""
from datetime import UTC, datetime
from alembic import op
import sqlalchemy as sa

revision = "0004_rive_character"
down_revision = "0003_experiment_rules"
branch_labels = None
depends_on = None


def upgrade():
    rules = sa.table("experiment_rules", sa.column("key", sa.String),
                     sa.column("enabled", sa.Boolean), sa.column("rollout_percentage", sa.Integer),
                     sa.column("allocation_salt", sa.String), sa.column("updated_at", sa.DateTime(timezone=True)))
    op.bulk_insert(rules, [{"key": "rive_character", "enabled": False, "rollout_percentage": 0,
                           "allocation_salt": "rive-character-v1", "updated_at": datetime.now(UTC)}])


def downgrade():
    rules = sa.table("experiment_rules", sa.column("key", sa.String))
    op.execute(rules.delete().where(rules.c.key == "rive_character"))
