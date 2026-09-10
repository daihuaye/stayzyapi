"""Run before migration 0007; never prints tokens. Requires explicit --execute."""
import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta
import httpx
import jwt
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def run(execute=False):
    settings=get_settings()
    engine=create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            rows=(await conn.execute(text("SELECT subject, refresh_token_encrypted FROM apple_identities"))).all()
        print(f"Apple credentials awaiting revocation: {len(rows)}")
        if not execute or not rows: return
        key_id=os.environ["STAYZY_APPLE_SIGN_IN_KEY_ID"]
        private_key=os.environ["STAYZY_APPLE_SIGN_IN_PRIVATE_KEY"]
        cipher=Fernet(os.environ["STAYZY_APPLE_SIGN_IN_TOKEN_KEY"].encode())
        async with httpx.AsyncClient(timeout=15) as client:
            for subject, encrypted in rows:
                now=datetime.now(UTC)
                secret=jwt.encode({"iss":settings.apple_team_id,"sub":settings.apple_bundle_id,
                    "aud":"https://appleid.apple.com","iat":now,"exp":now+timedelta(minutes=5)},
                    private_key,algorithm="ES256",headers={"kid":key_id})
                result=await client.post("https://appleid.apple.com/auth/revoke",data={
                    "client_id":settings.apple_bundle_id,"client_secret":secret,
                    "token":cipher.decrypt(encrypted.encode()).decode(),"token_type_hint":"refresh_token"})
                if result.status_code != 200: raise RuntimeError("Apple revocation failed; credential retained")
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM apple_identities WHERE subject=:subject AND refresh_token_encrypted=:encrypted"),
                        {"subject":subject,"encrypted":encrypted})
        print("Apple credential retirement complete")
    finally: await engine.dispose()

if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--execute",action="store_true")
    asyncio.run(run(parser.parse_args().execute))
