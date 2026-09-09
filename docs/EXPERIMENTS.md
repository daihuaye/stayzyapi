# Remote experiment rollout

Stayzy evaluates typed gates locally from `GET /v1/experiments`. The first gate,
`companion`, defaults to enabled and is seeded at 100%. Add future gates to
`ExperimentGate` with an off default and register corresponding backend rows in
an Alembic migration. Gates control presentation, never premium authorization.

## API contract

The public endpoint requires no account token or installation identifier:

```json
{
  "schemaVersion": 1,
  "rules": {
    "companion": {
      "enabled": true,
      "rolloutPercentage": 50,
      "allocationSalt": "companion-v1"
    }
  }
}
```

Percentages are integers from 0 through 100. Disabled always evaluates false.
Otherwise the client hashes UTF-8 `gateKey:allocationSalt:lowercase-installation-uuid`
with SHA-256, interprets the first four bytes as an unsigned big-endian integer,
and computes modulo 10,000. The gate is enabled when this bucket is less than
`rolloutPercentage * 100`. Increasing the percentage preserves participants.
The installation UUID stays in app-local UserDefaults and is independent of login
and profile selection. Reinstalling/resetting app data can create a new cohort.

Unknown rule keys are ignored, including unfamiliar payloads. Missing known keys
use bundled defaults. Unsupported versions and invalid known rules reject the
response without replacing valid cache.

## Refresh and session lifetime

Launch, foreground activation, and session creation request a nonblocking refresh.
The last attempt is saved before starting the request; all triggers share a
10-minute throttle, including after failure or app relaunch. Concurrent triggers
join the same task. There is no timer or background polling. A trigger after a
backward clock adjustment may refresh immediately rather than remaining stuck.

Successful responses persist with a separate successful-fetch timestamp. Rules
expire at 24 hours. Errors retain the old cache and its original timestamp.
Expired/missing cache uses bundled defaults; notably companion returns to on.
Both the cache and throttle are namespaced by API base URL.

Each new session saves its effective decisions under its UUID in separate local
UserDefaults metadata, leaving historical SwiftData schemas unchanged. Restoring
that session reuses its decisions through pauses and relaunches. Existing sessions
without metadata capture the current decisions once on restoration. Session
startup never waits for networking, so a response that arrives later affects a
subsequent session. Session metadata shares the API-environment namespace.

Companion off hides artwork and customization while keeping focus timing, camera
detection, voice selection/playback, haptics, and saved preferences available.

Debug builds retain `-StayzyDisableCompanion`, `STAYZY_COMPANION_ENABLED=0|1`,
`-StayzyForceStaticCompanion`, and `STAYZY_COMPANION_FORCE_STATIC=1`. Explicit
Debug settings win over remote rules; Release ignores these controls. UI-test
fixtures use a fixed experiment provider and support the same Debug overrides.

## Deployment and administration

1. Deploy the backend with migration `0003_experiment_rules` before shipping iOS.
   Run `alembic upgrade head` through the backend's normal deployment workflow.
2. Set the backend-only `STAYZY_EXPERIMENT_ADMIN_TOKEN` secret. Keep it out of
   app builds. Missing/blank configuration returns 503 for admin requests;
   missing/incorrect bearer authentication returns 401.
3. Use `GET /v1/admin/experiments` to inspect rules and
   `PUT /v1/admin/experiments/companion` to update the existing row. Supply the
   operator token as the Authorization Bearer header through a trusted API client.
   The JSON body is `{"enabled":true,"rolloutPercentage":50}`. Unknown keys return
   404; invalid percentages, types, or extra fields return 422. Salts cannot be
   changed through this API.
4. Verify public GET reflects the update immediately. Both GET endpoints read the
   database on every request and return `Cache-Control: no-store`.
5. Verify 0%, an intermediate percentage, and 100% in staging. New sessions should
   reflect fetched rules; ongoing sessions should not change. Check
   `experiment.updated` logs and existing request/correlation IDs. Audit logs
   include old/new rollout values without bearer tokens.

Rollback sets `enabled` to false or percentage to zero. It takes effect only after
an eligible successful client refresh and at a new session boundary. It is not an
immediate emergency shutdown; offline cache expiry returns to bundled defaults.
No deployment, production rule update, or secret provisioning is performed by the
implementation itself.
