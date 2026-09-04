# Stayzy API

FastAPI service for passwordless email authentication, App Store entitlements,
provider-neutral companion catalogs, and private voice-pack downloads.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m app.jobs.seed_catalog
.venv/bin/uvicorn app.main:app --reload
```

Development defaults to SQLite. Set `STAYZY_DATABASE_URL` to Railway's
`DATABASE_URL`; the service converts its standard `postgresql://` scheme to
the async driver automatically. Railway production also requires ES256 signing
keys, SendGrid configuration, Apple root
certificates, and Railway Bucket credentials described in `.env.example`.
Set `STAYZY_APPLE_TEAM_ID` so the service can publish the Apple App Site
Association response for `https://links.stayzy.app/auth/verify`.
The production command disables raw request access logs so magic-link query
tokens cannot appear in application logs.

Export the backend ES256 public key to the iOS app's
`STAYZY_ENTITLEMENT_PUBLIC_KEY` Info.plist value as PEM or base64-encoded DER.
The app verifies this signature before caching Premium playback access for
offline use. Online development responses work without the key but are never
cached as offline authority.

## Voice-pack publishing

Run migrations and seed the provider-private catalog, then execute the builder
as a Railway one-off job:

```bash
python -m app.jobs.build_voice_pack --voice-id voice_willow --locale en-US --preflight-only
python -m app.jobs.build_voice_pack --voice-id voice_willow --locale en-US
```

Create a Railway Bucket first. For local development, copy these values from the
bucket's **Credentials** tab into `.env`:

| Railway credential | Stayzy setting |
| --- | --- |
| `BUCKET` (or CLI output `AWS_S3_BUCKET_NAME`) | `STAYZY_BUCKET` |
| `ACCESS_KEY_ID` | `STAYZY_BUCKET_ACCESS_KEY_ID` |
| `SECRET_ACCESS_KEY` | `STAYZY_BUCKET_SECRET_ACCESS_KEY` |
| `ENDPOINT` (or CLI output `AWS_ENDPOINT_URL`) | `STAYZY_BUCKET_ENDPOINT` |
| `REGION` | `STAYZY_BUCKET_REGION` |

For a Railway service or one-off job, use reference variables instead of copying
credentials. If the bucket service is named `voice-packs`, configure:

```text
STAYZY_BUCKET=${{voice-packs.BUCKET}}
STAYZY_BUCKET_ACCESS_KEY_ID=${{voice-packs.ACCESS_KEY_ID}}
STAYZY_BUCKET_SECRET_ACCESS_KEY=${{voice-packs.SECRET_ACCESS_KEY}}
STAYZY_BUCKET_ENDPOINT=${{voice-packs.ENDPOINT}}
STAYZY_BUCKET_REGION=${{voice-packs.REGION}}
```

Run `--preflight-only` first. It checks the phrase catalog, database voice,
locale, and bucket access without calling OpenAI or generating audio.

The deployed builder reads its bundled catalog from
`app/assets/CompanionPhrases.json`, so it does not depend on the iOS repository
being present in Railway. After changing the iOS phrase catalog, synchronize the
backend copy before publishing a new pack:

```bash
cp ../stayzy/stayzy/Companion/Assets/CompanionPhrases.json app/assets/CompanionPhrases.json
```

The job reads only approved phrases from the app catalog, generates AAC assets,
validates completeness, uploads an immutable archive and manifest, and switches
the active database version transactionally. The OpenAI key belongs only on this
job, not on the iOS client or FastAPI web service.

## Deployment

Connect Railway to this repository root. `railway.toml` builds the root
`Dockerfile`. Run this pre-deploy command:

```bash
alembic upgrade head
```

Use a dedicated SendGrid key restricted to Mail Send. Configure a signed
SendGrid Event Webhook at `/v1/webhooks/sendgrid/events` and App Store Server
Notifications V2 at `/v1/webhooks/app-store`.


## Diagnosing API and sign-in failures

Redeploy the backend after updating this code. Logs are newline-delimited JSON at
INFO level by default; no additional environment variable is needed. Every HTTP
response includes a generated `X-Request-ID`. The iOS error alert displays this
reference when available. Search Railway deployment logs for that reference.
`request.started` proves the API received the request; `request.completed` records
the matched route template, HTTP status and elapsed milliseconds. Unknown paths
are logged as `unmatched`, without their contents. `request.exception` includes
exception class chains and stack locations, but no exception messages, SQL or locals.
Handled errors and validation errors use the standard `detail.code/message`
response with a request reference. Unexpected failures return `internal_error`.

For sign-in, follow these events in order:

- `auth.magic_link_requested`: the validated email request reached the handler.
- `auth.magic_link_throttled`: the email/IP cooldown prevented another send.
- `email.skipped`: required SendGrid settings are missing; `missing` lists names only.
- `email.send_started`: the SendGrid call is beginning.
- `email.accepted`: SendGrid returned 202; this means queued, not delivered.
- `email.rejected`: includes provider HTTP status and a static troubleshooting hint.
  Check the API key for 401, verified sender and Mail Send permission for 403,
  the active dynamic template/payload for 400, and rate limits for 429.
- `email.send_failed`: transport failure; examine the safe exception class.
- `auth.magic_link_processed`: the final send state was committed to the database.
- `email.delivery_events_processed`: verified SendGrid webhook events, counted by
  delivered/deferred/bounce/blocked/dropped. Configure the signed webhook to see these.

The magic-link endpoint still returns the same 202 body for accepted, rejected,
or throttled sends. This prevents account enumeration; the UI's confirmation is
not evidence that SendGrid delivered an email. Do not repeatedly request links
while diagnosing: the limits are three per email per 15 minutes and ten per IP
per hour. Use SendGrid Email Activity to investigate delivery after acceptance.

`service.started` reports whether email settings are present (never their values).
`readiness.failed` identifies database versus bucket failures; storage configuration
and bucket checks have additional safe diagnostics. A successful `/health/live`
does not establish that either dependency is available.

Never enable raw HTTP/SQL debug logging in production. These diagnostics exclude
emails, credentials, request/response bodies, query strings, raw paths, and
presigned URLs. Provider response bodies are intentionally not printed. Existing
reverse-proxy/platform logging is configured separately.
