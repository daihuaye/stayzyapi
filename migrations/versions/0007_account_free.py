"""Preserve purchase grants while retiring customer accounts.

Run the revoke_customer_apple_tokens job before this migration. A nonempty
Apple identity table blocks retirement, rather than discarding live credentials.
"""
from alembic import op
import sqlalchemy as sa
revision = "0007_account_free"
down_revision = "0006_apple_sign_in"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM apple_identities")).scalar():
        raise RuntimeError("Revoke customer Apple tokens before account retirement")
    before = bind.execute(sa.text("SELECT count(*) FROM store_transactions")).scalar()
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("store_transactions", naming_convention=naming) as batch:
        batch.add_column(sa.Column("ownership_type", sa.String(24), nullable=False, server_default="PURCHASED"))
        batch.add_column(sa.Column("app_transaction_id", sa.String(128)))
        batch.drop_index("ix_store_transactions_user_id")
        foreign = sa.inspect(bind).get_foreign_keys("store_transactions")
        fk_name = next((v["name"] for v in foreign if v["constrained_columns"] == ["user_id"]), None)
        batch.drop_constraint(fk_name or "fk_store_transactions_user_id_users", type_="foreignkey")
        # The original migration's PostgreSQL-generated constraint name differs
        # from SQLite's naming convention.
        unique = sa.inspect(bind).get_unique_constraints("store_transactions")
        name = next((v["name"] for v in unique if v["column_names"] == ["transaction_id"]), None)
        batch.drop_constraint(name or "uq_store_transactions_transaction_id", type_="unique")
        batch.drop_column("user_id")
        batch.create_unique_constraint("uq_store_environment_transaction", ["environment", "transaction_id"])
        batch.create_index("ix_store_transactions_app_transaction_id", ["app_transaction_id"])
    after = bind.execute(sa.text("SELECT count(*) FROM store_transactions")).scalar()
    if before != after: raise RuntimeError("Purchase ledger count changed during migration")
    for table in ("apple_identities", "auth_sessions", "magic_links", "email_delivery_events", "entitlements", "users"):
        op.drop_table(table)

def downgrade():
    raise RuntimeError("Customer retirement cannot recreate personal data. Restore a pre-cutover backup instead.")
