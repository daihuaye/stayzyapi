# Willow Cedar / Harbor Nova release

Generated 2026-09-09 UTC. Names, saved voice IDs, model, instructions, locale, and
all 500 phrases are unchanged. The [official speech reference](https://developers.openai.com/api/reference/cli/resources/audio/subresources/speech/methods/create)
lists Cedar and Nova as supported provider voices.

| Voice | Pack version | Delivery version | ZIP bytes |
| --- | --- | --- | --- |
| Willow (`voice_willow`) | `2026.09.09.0331-cedar` | `warm-calm-v3-cedar` | 16,271,066 |
| Harbor (`voice_harbor`) | `2026.09.09.0331-nova` | `grounded-v3-nova` | 16,848,814 |

Model: `gpt-4o-mini-tts-2025-12-15`. Locale: `en-US`.
Phrase catalog: `3414ec190c3df9a4`.

Archive SHA-256:

- Willow: `48a1ffe840918d5c6097df36fc3bbfeb09584b2167db2fec88898c54e45fa53d`
- Harbor: `2426ce48166342094e57b39f24468406e1d951187fec95f0fffe7396d8cb36bd`

Preview SHA-256:

- Willow (33,729 bytes): `7bf66d623e5b592869b2332aa110e77e772d11eb10b726e375a6a975434d9aeb`
- Harbor (32,166 bytes): `4ca829e6ac4d8f74fcb5904f36e4f76cc537eb8d35ee6a74807cb37362f4448c`

## Completed

- Both provider/model/delivery preflights passed against the configured bucket.
- Generated and uploaded 500 phrases plus a preview for each voice.
- Downloaded both releases from the bucket; checked ZIP format, exact phrase
  coverage, manifest identity, sizes, SHA-256, delivery versions, ADTS frames,
  and full ffmpeg decoding of all 1,002 AAC files.
- Replaced the complete iOS `WillowVoice.bundle` with Cedar, including preview.
  Old Coral contents are absent from the app bundle.
- Added version-aware refresh and validated activation before obsolete local
  directories are removed. Offline, denied, interrupted, corrupt, cancelled,
  or incomplete upgrades retain the active pack and retry on eligible refresh.
- No public API or persisted profile schema change. Harbor keeps `voice_harbor`.

## Verification results

- Backend: 72 tests passed (seed preservation, provider forwarding, preflight
  output, registration, atomic preview pairing, and existing API tests).
- iOS simulator: 32 Swift Testing tests plus 4 XCTest playback tests passed,
  including parameterized corrupt/cancelled and offline/access/authorization/
  interruption cases. Cedar preview and offline playback used the real bundle.
- Local Release build for generic iOS device: passed, signing disabled. The built
  `.app` contains exactly 501 Cedar AAC files; all phrase and preview checksums
  match the downloaded release.
- Persisted Harbor selection was saved before replacement and reloaded after
  activation; it remains `voice_harbor`.
- Backend and iOS phrase catalog source files are byte-for-byte identical.
- Previous server ZIPs confirmed retained: Willow `2026.09.04.2101`; Harbor
  `2026.09.04.2040` and `2026.09.06.2215`.
- The retry/download tests use controlled service doubles with real local pack
  validation. Live authenticated Railway downloads also passed as recorded below.

## Hosted publication completed

Published 2026-09-09 UTC to the existing prelaunch Railway service:
`superb-youth` / `production` / `stayzyapi`, serving
`https://stayzyapi-production.up.railway.app`. The service retains its staging
application environment and Apple Sandbox configuration.

Publication deployment: `f99e4f16-0941-4b34-9899-f49e067cf910`.
Final normal deployment: `dce0aff5-6b69-4a38-b43a-90788fa5340b` — SUCCESS.
The final image excludes the temporary activation/verification job; its effective
pre-deploy command is restored to `alembic upgrade head`.
Both hosted preflights passed with the requested provider voices, unchanged model,
and delivery versions. Hosted reseeding preserved all existing preview keys.

The hosted catalog and authenticated download endpoints returned both new pack
versions and exact ZIP sizes/checksums listed above. Downloaded archives matched
the API manifests and all phrase file checksums. Both catalog preview URLs
returned the exact versioned previews. Unauthenticated downloads returned 401;
an authenticated account without access received 403 for Harbor.

Verification used an isolated five-minute prelaunch trial/session fixture, with
no email or App Store calls. Its account, session, and transaction fixture were
removed immediately after verification. Existing user accounts were untouched.

Harbor `2026.09.06.2215` is retired; Nova is its only active record. Willow's
hosted database previously had no active pack record; Cedar is now active. All
previous bucket archives remain retained for rollback.

Activation ran as a temporary pre-deploy job inside the existing Railway service,
using its private PostgreSQL connection and existing credentials. No SSH keys or
public database access were added. The original migration-only pre-deploy command
was restored after the job completed.

Equivalent activation commands for this release (run inside the API environment):

```bash
python -m app.jobs.seed_catalog
python -m app.jobs.build_voice_pack --voice-id voice_willow --locale en-US --preflight-only
python -m app.jobs.build_voice_pack --voice-id voice_harbor --locale en-US --preflight-only
python -m app.jobs.register_voice_pack --voice-id voice_willow --locale en-US --manifest-key voice-packs/voice_willow/en-US/3414ec190c3df9a4/2026.09.09.0331-cedar/manifest.json --preview-key voice-previews/voice_willow/en-US/2026.09.09.0331-cedar/preview.aac
python -m app.jobs.register_voice_pack --voice-id voice_harbor --locale en-US --manifest-key voice-packs/voice_harbor/en-US/3414ec190c3df9a4/2026.09.09.0331-nova/manifest.json --preview-key voice-previews/voice_harbor/en-US/2026.09.09.0331-nova/preview.aac
```

Do not regenerate these versions. Registration reuses verified objects and
transactionally retires previous active records without deleting rollback files.
Use new versions for any subsequent generation.

The local iOS tests cover fresh installation, automatic Echo-to-Nova replacement,
retention on interruption/offline/access denial, retry, playback, and persisted
Harbor selection. The local Release build contains the validated Cedar bundle.
App Store submission is outside this release.

## Reproduce archive validation

Requires local ffmpeg plus the configured bucket credentials. Choose new empty
output directories. This performs reads only against object storage.

```bash
python -m scripts.validate_voice_release --voice-id voice_willow --version 2026.09.09.0331-cedar --delivery warm-calm-v3-cedar --output /tmp/willow-release-check
python -m scripts.validate_voice_release --voice-id voice_harbor --version 2026.09.09.0331-nova --delivery grounded-v3-nova --output /tmp/harbor-release-check
```

Each output directory contains `pack.zip`, `manifest.json`, an extracted `bundle`
with its preview, and `verification.json`. Import Willow only after validation;
replace the whole bundle so obsolete files cannot ship alongside Cedar.

## Rollback

Retain all previous bucket archives, manifests, and previews. Re-register a known
previous manifest to reactivate it; pair the appropriate old preview in the same
transaction (legacy unversioned preview keys require an operator transaction).
Restore the previous Willow bundle from source control if an app rollback is
needed. On-device cleanup occurs only after a replacement passes validation and
its active metadata is saved atomically; failed replacement never deletes Echo.
