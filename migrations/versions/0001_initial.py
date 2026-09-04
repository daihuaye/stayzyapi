"""Create authentication, purchase, and voice catalog tables."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "magic_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("requested_ip_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("send_state", sa.String(24), nullable=False),
        sa.Column("sendgrid_message_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_magic_links_email_created", "magic_links", ["email", "created_at"])
    op.create_index("ix_magic_links_requested_ip_hash", "magic_links", ["requested_ip_hash"])
    op.create_index("ix_magic_links_expires_at", "magic_links", ["expires_at"])
    op.create_index("ix_magic_links_sendgrid_message_id", "magic_links", ["sendgrid_message_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "email_delivery_events",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("sendgrid_message_id", sa.String(255)),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_delivery_events_sendgrid_message_id", "email_delivery_events", ["sendgrid_message_id"])

    op.create_table(
        "store_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("original_transaction_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("billing_subject", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("environment", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transaction_id"),
    )
    for column in ["transaction_id", "original_transaction_id", "user_id", "billing_subject", "product_id", "status", "expires_at"]:
        op.create_index(f"ix_store_transactions_{column}", "store_transactions", [column])

    op.create_table(
        "entitlements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_key", sa.String(80), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "feature_key", name="uq_entitlement_feature"),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_index("ix_entitlements_feature_key", "entitlements", ["feature_key"])
    op.create_index("ix_entitlements_status", "entitlements", ["status"])

    op.create_table(
        "voice_definitions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("supported_locales", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_voice_id", sa.String(160), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("instruction_version", sa.String(80), nullable=False),
        sa.Column("preview_object_key", sa.String(500)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_voice_definitions_tier", "voice_definitions", ["tier"])
    op.create_index("ix_voice_definitions_status", "voice_definitions", ["status"])

    op.create_table(
        "voice_pack_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("voice_id", sa.String(80), sa.ForeignKey("voice_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.Column("catalog_version", sa.String(80), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("archive_object_key", sa.String(500), nullable=False),
        sa.Column("manifest_object_key", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("voice_id", "locale", "version", name="uq_voice_pack_version"),
    )
    op.create_index("ix_voice_pack_versions_voice_id", "voice_pack_versions", ["voice_id"])
    op.create_index("ix_voice_pack_versions_locale", "voice_pack_versions", ["locale"])
    op.create_index("ix_voice_pack_versions_status", "voice_pack_versions", ["status"])

    op.create_table(
        "companion_definitions",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_companion_definitions_tier", "companion_definitions", ["tier"])
    op.create_index("ix_companion_definitions_status", "companion_definitions", ["status"])

    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_receipts_provider", "webhook_receipts", ["provider"])


def downgrade() -> None:
    for table in [
        "webhook_receipts",
        "companion_definitions",
        "voice_pack_versions",
        "voice_definitions",
        "entitlements",
        "store_transactions",
        "email_delivery_events",
        "auth_sessions",
        "magic_links",
        "users",
    ]:
        op.drop_table(table)
