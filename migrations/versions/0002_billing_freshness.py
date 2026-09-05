"""Track Apple ordering and billing grace separately from playback grace."""
from alembic import op
import sqlalchemy as sa
revision = "0002_billing_freshness"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("store_transactions", sa.Column("apple_signed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("store_transactions", sa.Column("billing_grace_expires_at", sa.DateTime(timezone=True), nullable=True))

def downgrade():
    op.drop_column("store_transactions", "billing_grace_expires_at")
    op.drop_column("store_transactions", "apple_signed_at")
