"""Indexes for restricted telemetry reporting."""
from alembic import op
revision = "0009_telemetry_reporting"
down_revision = "0008_telemetry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_telemetry_reporting_period", "telemetry_events", ["environment", "occurred_at", "received_at"])
    op.create_index("ix_telemetry_reporting_identity", "telemetry_events", ["environment", "installation_id", "session_id", "sequence", "event_id"])


def downgrade():
    op.drop_index("ix_telemetry_reporting_identity", table_name="telemetry_events")
    op.drop_index("ix_telemetry_reporting_period", table_name="telemetry_events")
