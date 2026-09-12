"""Anonymous telemetry and latest session summaries."""
from alembic import op
import sqlalchemy as sa
revision = "0008_telemetry"
down_revision = "0007_account_free"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("telemetry_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36)), sa.Column("sequence", sa.Integer()),
        sa.Column("name", sa.String(80), nullable=False), sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.Column("payload", sa.JSON(), nullable=False))
    op.create_index("ix_telemetry_session", "telemetry_events", ["installation_id", "session_id", "sequence"])
    op.create_index("ix_telemetry_usage", "telemetry_events", ["environment", "name", "occurred_at"])
    op.create_index("ix_telemetry_retention", "telemetry_events", ["received_at"])
    op.create_table("telemetry_sessions", sa.Column("installation_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), primary_key=True), sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("summary", sa.JSON(), nullable=False))
    op.create_index("ix_telemetry_sessions_updated_at", "telemetry_sessions", ["updated_at"])

def downgrade():
    op.drop_table("telemetry_sessions")
    op.drop_table("telemetry_events")
