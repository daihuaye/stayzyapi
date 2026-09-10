"""Store verified Apple identities and encrypted refresh tokens for revocation."""
from alembic import op
import sqlalchemy as sa

revision = "0006_apple_sign_in"
down_revision = "0005_administrators"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("apple_identities",
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False))
    op.create_index("ix_apple_identities_user_id", "apple_identities", ["user_id"])


def downgrade():
    op.drop_index("ix_apple_identities_user_id", table_name="apple_identities")
    op.drop_table("apple_identities")
