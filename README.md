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
| `BUCKET` | `STAYZY_BUCKET` |
| `ACCESS_KEY_ID` | `STAYZY_BUCKET_ACCESS_KEY_ID` |
| `SECRET_ACCESS_KEY` | `STAYZY_BUCKET_SECRET_ACCESS_KEY` |
| `ENDPOINT` | `STAYZY_BUCKET_ENDPOINT` |
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
