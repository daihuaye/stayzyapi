# Telemetry admin reporting

The Stayzyweb `/admin/telemetry` page uses authenticated, read-only endpoints. Both active administrators and owners may read reports. Anonymous ingestion credentials and installation IDs do not grant reporting access. All responses use `Cache-Control: no-store` and the existing request/correlation diagnostics. No event bodies are logged by reporting.

## Deployment

1. Deploy stayzyapi including migration `0009_telemetry_reporting` and run `alembic upgrade head` using the normal deployment database configuration.
2. Verify the existing administrator login and authenticated `GET /v1/admin/telemetry/overview` return successfully. There is no public telemetry-read endpoint.
3. Deploy stayzyweb with its existing `STAYZY_API_BASE_URL` (HTTPS in production) and `STAYZY_SESSION_SECRET`. No new production secrets or environment settings are required.
4. Open **Telemetry** in the admin sidebar. The default is production, the last seven days, UTC. Debug/test traffic requires an explicit environment selection. Refresh is manual.

If the API reporting update is missing, the web app displays an actionable deployment error rather than presenting empty charts. Roll back the web deployment independently if required; the index-only migration does not change ingestion or raw events. Existing collection controls and the 90-day receipt-based purge remain unchanged.

## Endpoints and filters

All paths are under `/v1/admin/telemetry`:

- `/overview`: usage, cohort outcomes, attempt/session funnels, state-duration summaries, delivery lag, and snapshot coverage.
- `/health`: camera window/diagnostic aggregates plus session-cohort attribution totals.
- `/sessions`: session explorer; at most 100 rows, default 50.
- `/sessions/{installation_id}/{session_id}`: full retained lifecycle and selected outcome timeline.
- `/sessions/{installation_id}/{session_id}/events`: ordered event log; at most 100 rows, default 50.

Response schemas appear in the development OpenAPI documentation. Filters: `start`, `end`, `as_of` (timezone-aware timestamps), `environment` (`production`, `debug`, `test`), and `app_version`. Ranges are `[start, end)`, positive and at most 90 days. The web custom-date control converts an inclusive UTC end date to the next midnight.

The server returns `as_of`; clients must reuse it and identical filters on subsequent cursor pages. A changed filter or malformed cursor is rejected. Cursors are opaque query positions, not credentials. The cutoff includes events received by `as_of` and excludes records older than its 90-day receipt window. Retention deletions between requests can still remove records; a refresh is a bounded receipt snapshot, not a database snapshot transaction.

Session filters: exact `session_id`, `status`, latest `state`, `buddy`, `errors`, `possible_drop_off`, `stage`, and `reason`. `wait_state` matches recorded time in a previous state. `activity=true` selects sessions with events in the occurrence window instead of the creation cohort; diagnostic drill-downs use this so an error on an older session remains inspectable. Session details ignore cohort dates and app-version filtering and inspect the full retained identity in the selected environment.

## Interpretation

- Session identity is `(installation_id, session_id)`. Sequence determines lifecycle order; event ID breaks sequence ties deterministically. A higher-sequence resume/state transition supersedes an earlier End for Now or failure. The reporting code does not rely on the ingestion summary's receipt ordering.
- Completion/failure rates use **all observed sessions in the cohort** as their displayed denominator, including adopted sessions. The funnel excludes adopted sessions without a retained `session.started` event. Recent incomplete sessions are not abandoned sessions.
- Start-attempt conversion is a separate cohort keyed by installation and attempt ID. Access gates are optional branches. Setup views without attempt correlation are standalone event counts. Session funnel stages require evidence for every preceding stage, so missing telemetry can lower conversion.
- Session cohorts start in the selected occurrence range and are followed through `as_of`, including outcomes after the range. Ordinary usage, diagnostics and waiting-duration charts measure events occurring inside the range. Daily outcome charts count outcome **events**, not unique sessions.
- Possible drop-off requires an unfinished lifecycle, no explicit latest manual break, and both last occurrence and receipt at least 24 hours old. Missing outcomes cannot establish force quits, crashes, uninstalls, or abandonment. No duration is extrapolated through gaps.
- Foreground time sums `app.usage.duration`; cumulative foreground totals are never summed. Progress and buddy time come from the latest cumulative summary. State duration belongs to `previous_state`. Overlapping state, presence, break, and buddy lanes must not be added.
- Camera processing means are weighted by sample count; maximums are not percentiles. Startup latencies describe successful first frames. Attempts/recoveries are counts rather than an invented success rate. Observation counts are not recognition accuracy.
- Buddy attribution uses latest cumulative totals for the selected session cohort. Known, unknown, and estimated time are displayed separately: estimated can overlap known credit. Epoch detail uses visits from the selected snapshot; partial snapshots have partial coverage, and legacy identities without epochs remain unavailable. Camera degraded duration is accumulated separately by epoch.
- An outcome references its expected snapshot ID/revision. Distinct part indexes determine completeness, including entirely missing snapshots. Detail chooses the latest outcome-referenced snapshot and retains earlier metadata/outcomes. Duplicate interval IDs are merged; snapshot state intervals and live state events are reconciled without adding them twice.
- The progress plot samples evenly to at most 500 checkpoints, preserving the endpoints; it labels the full count. The marker list shows the latest 500. The paginated event log retains access to all available events. Interval tables paginate 50 rows.
- Delivery lag reports mean/maximum nonnegative lag; negative lag is counted separately as clock skew. Reported client drops and incomplete latest snapshots expose known gaps, not all possible missing telemetry.

## Verification and operations

Run `.venv/bin/pytest` in stayzyapi and `pnpm test`, `pnpm lint`, `pnpm build` in stayzyweb. Tests cover authorization, date/cursor validation, cutoff ordering, duration deltas, continuation, delayed receipt, drop-off exclusions, partial/missing snapshots, epochs, migration, and UI filtering/refresh/drill-down.

A local synthetic SQLite benchmark across 80,655 events / 240 sessions / 89 days measured approximately 2.3 seconds for overview, 0.7 seconds for health, 0.14 seconds for a session page, and 0.013 seconds for detail. This is a local reference, not a PostgreSQL production capacity guarantee. Verify query plans and latency at production volume before broad operational reliance. Queries aggregate server-side and use indexed identity lookups; no raw event corpus is downloaded to build overview charts.

Monitor reporting HTTP failures/latency, the delivery-quality panel, incomplete snapshots, and client drops. Existing ingestion monitoring still owns rejected events; ingestion rejections cannot be reconstructed from accepted-event storage. Missing measurements display as unavailable.
