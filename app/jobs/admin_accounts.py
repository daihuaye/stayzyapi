"""Run with `python -m app.jobs.admin_accounts bootstrap-owner` or `recover-owner`."""
from __future__ import annotations
import argparse
import asyncio
import getpass
import sys
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from app.admin_models import Administrator
from app.admin_security import hash_password, revoke_all, security_lock
from app.db import SessionFactory
from app.observability import emit
from app.security import normalize_email


async def manage_owner(command: str, email: str, password: str) -> None:
    if not 15 <= len(password) <= 128:
        raise ValueError("Password must contain 15–128 characters.")
    email = normalize_email(str(TypeAdapter(EmailStr).validate_python(email)))
    async with SessionFactory() as db:
        await security_lock(db)
        account = await db.scalar(select(Administrator).where(Administrator.email == email))
        if command == "bootstrap-owner":
            owner = await db.scalar(select(Administrator.id).where(Administrator.role == "owner").limit(1))
            if owner or account:
                raise ValueError("An owner or this account already exists. Bootstrap refuses to overwrite accounts.")
            account = Administrator(email=email, role="owner", active=True,
                                    password_hash=await hash_password(password), must_change_password=True)
            db.add(account)
        else:
            if not account or not account.active or account.role != "owner":
                raise ValueError("Recovery requires an existing active owner.")
            account.password_hash = await hash_password(password)
            account.must_change_password = True
            await revoke_all(db, account.id)
        await db.commit()
        emit("admin.owner_bootstrapped" if command == "bootstrap-owner" else "admin.owner_recovered", administrator_id=account.id)


def main():
    parser = argparse.ArgumentParser(description="Manage Stayzy owner access using hidden password prompts.")
    parser.add_argument("command", choices=["bootstrap-owner", "recover-owner"])
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.exit(1, "Run this command in an interactive terminal; passwords must not be piped.\n")
    email = input("Owner email: ")
    password = getpass.getpass("Temporary password (15–128 characters): ")
    if password != getpass.getpass("Confirm temporary password: "):
        parser.exit(1, "Passwords do not match.\n")
    try:
        asyncio.run(manage_owner(args.command, email, password))
    except ValueError:
        parser.exit(1, "Owner operation refused. Check the email, password requirements, and existing owner state.\n")
    print("Owner access updated. Sign in and choose a new password before continuing.")


if __name__ == "__main__":
    main()
