import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.mark.parametrize("has_apple_identity", [False, True])
def test_purchase_preservation_and_credential_guard(tmp_path, has_apple_identity):
    database=tmp_path/"retirement.db"
    env=dict(os.environ, STAYZY_ENVIRONMENT="test", STAYZY_DATABASE_URL=f"sqlite+aiosqlite:///{database}")
    root=Path(__file__).resolve().parents[1]
    def migrate(target):
        return subprocess.run([sys.executable,"-m","alembic","upgrade",target],cwd=root,env=env,capture_output=True,text=True)
    assert migrate("0006_apple_sign_in").returncode==0
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO users(id,email,status,created_at) VALUES('customer','old@example.test','active',CURRENT_TIMESTAMP)")
        for id,status,revoked in [("valid","active",None),("refunded","revoked","2026-09-01")]:
            db.execute("INSERT INTO store_transactions(id,transaction_id,original_transaction_id,user_id,billing_subject,product_id,environment,status,purchased_at,revoked_at,updated_at) VALUES(?,?,?,'customer','subject','lifetime','Sandbox',?,CURRENT_TIMESTAMP,?,CURRENT_TIMESTAMP)",(id,id,id,status,revoked))
        if has_apple_identity:
            db.execute("INSERT INTO apple_identities(subject,user_id,refresh_token_encrypted) VALUES('apple','customer','encrypted-secret')")
        before=db.execute("SELECT transaction_id,status,revoked_at,purchased_at FROM store_transactions ORDER BY transaction_id").fetchall()
    result=migrate("head")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT transaction_id,status,revoked_at,purchased_at FROM store_transactions ORDER BY transaction_id").fetchall()==before
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if has_apple_identity:
            assert result.returncode!=0 and "Revoke customer Apple tokens" in result.stderr
            assert "users" in names and "apple_identities" in names
            assert db.execute("SELECT refresh_token_encrypted FROM apple_identities").fetchone()[0]=="encrypted-secret"
        else:
            assert result.returncode==0, result.stderr
            assert not names.intersection({"users","auth_sessions","entitlements","magic_links","apple_identities"})
            assert "administrators" in names
            assert db.execute("SELECT ownership_type FROM store_transactions").fetchall()==[("PURCHASED",),("PURCHASED",)]
