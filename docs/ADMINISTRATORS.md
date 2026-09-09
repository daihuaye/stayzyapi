# Administrator accounts

Administrator identities are separate from iOS customer accounts. Owners manage
accounts and flights; admins manage flights. There is no public signup. Shared
experiment-token authentication is removed with this release.

## Railway backend setup and cutover

1. Install the updated backend dependencies (including `argon2-cffi`). Run
   `alembic upgrade head` against the intended database. Migration
   `0005_administrators` creates the account, session, reset, throttle, and lock
   tables. It does not create any account or modify rollout values.
2. In an interactive terminal **inside the intended backend environment**, run:

   ```sh
   python -m app.jobs.admin_accounts bootstrap-owner
   ```

   Enter the owner email and a temporary password through the hidden prompts.
   Passwords are 15–128 characters and are not trimmed. The command refuses to
   overwrite an account or run once any owner exists. There are no password
   command-line arguments; do not pipe passwords into the command.
3. Deploy the backend and web release in a coordinated admin maintenance window.
   There is no shared-token compatibility fallback. Old web deployments stop
   authenticating after the backend cutover. The public experiment endpoint and
   iOS customer authentication remain unchanged.
4. Sign in on the updated website, change the temporary password, then sign in
   again. Owners can now create accounts through **Administrators**. Share each
   temporary password separately; account creation does not send invitation mail.
5. Remove the obsolete shared admin-token environment variable. It is no longer
   read. Existing web cookies are rejected by the versioned session cookie.

The command uses the configured database; running it locally does not create an
owner in Railway. Never apply these steps to production as part of automated tests.

## Configuration by service

| Service | Variable | Purpose |
| --- | --- | --- |
| Railway backend | `STAYZY_ADMIN_WEB_URL` | Fixed web origin for password recovery, e.g. `https://stayzyweb.vercel.app`. HTTPS required in production; no path, query, credentials, or fragment. |
| Railway backend | `STAYZY_SENDGRID_ADMIN_RESET_TEMPLATE_ID` | Dedicated administrator recovery template. |
| Railway backend | Existing SendGrid API key and sender | Reuse the configured mail service. |
| Railway backend | `STAYZY_ADMIN_LOGIN_BUDGET` | Global login/change-password attempts per minute; default 100. |
| Railway backend | `STAYZY_ADMIN_RESET_BUDGET` | Global reset requests per minute; default 30. |
| Vercel website | `STAYZY_API_BASE_URL` | Railway API origin without `/v1`; HTTPS in production. |
| Vercel website | `STAYZY_SESSION_SECRET` | Random 32+ character website session-encryption secret; not an account password. |

Redeploy the affected service after changing deployed variables. Local web
settings belong in `.env.local`; local backend settings belong in its `.env`.
Use a separate database and secrets for local/staging verification.

## Recovery mail

The SendGrid dynamic template receives `subject`, `reset_link`, and
`expires_minutes` (30). Use `{{reset_link}}` for the reset button and explain
that the request can be ignored when unsolicited. Click and open tracking are
disabled. Do not add third-party tracking to recovery pages.

Reset URLs carry the token in the fragment. The web form copies it into transient
memory and immediately removes the fragment, so a refresh requires reopening the
original email link. Opening a link does not consume it. Password submission
consumes it atomically. New requests invalidate older links; deactivation and
password changes invalidate outstanding links. Successful reset revokes all
sessions and requires a new login.

Forgot-password responses are generic, including for disabled/missing accounts,
throttling, and email delivery failure. Acknowledgment does not establish inbox
delivery. Safe `admin.email_processed`, `admin.reset_requested`, and
`admin.reset_delivery_failed` events report outcomes without secrets.

## Emergency owner recovery

When email recovery is unavailable, an operator with backend shell access can run:

```sh
python -m app.jobs.admin_accounts recover-owner
```

This requires an existing active owner. It sets a temporary password through
hidden prompts, revokes sessions/reset links, and requires a password change.
It does not silently reactivate disabled accounts or bootstrap another owner.

## Session and authorization model

Passwords use Argon2id (19 MiB, two iterations, parallelism one). Opaque random
session tokens have an absolute eight-hour expiry and are stored only as SHA-256
hashes in the backend. The website encrypts the token in an HttpOnly, SameSite=Strict,
Secure-in-production cookie. Every backend request checks current status, role,
expiry, and revocation. Temporary-password sessions may only call `me`,
`change-password`, and `logout`. Owners cannot demote or deactivate themselves.

A database mutex row serializes administrator security transitions across
replicas, including SQLite development and PostgreSQL production. Login attempts
are reserved before Argon2 verification; failed per-email attempts are limited to
five per 15 minutes. Reset requests allow three per email per 15 minutes. Attempts
older than one day are pruned during subsequent authentication requests. This
simple lock is intended for a small administrator team; monitor contention if
admin traffic grows. It never serializes public iOS configuration reads.

Protected experiment mutations log the acting administrator ID. Passwords,
password hashes, cookies, reset URLs, and tokens must never be added to logs.
Logout clears the browser cookie even if the backend is unavailable, but then
shows that remote revocation was not confirmed; the backend session still expires.

## Verification

Run `python -m pytest -q` in the backend environment and `pnpm test`, `pnpm lint`,
and `pnpm build` in the web repository. Tests create isolated temporary databases,
mock SendGrid, and never use real account credentials. Migration upgrade/downgrade
is tested against SQLite. Verify the migration on staging PostgreSQL before
production deployment. MFA, account deletion, and email changes are not included.
