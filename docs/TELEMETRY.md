# Session and usage telemetry

## Collection contract

Anonymous analytics is enabled by default, with **Share anonymous usage and diagnostics** in Settings. Disabling clears the local outbox and analytics metadata and stops collection and delivery. Re-enabling rotates the random installation ID. Session records remain local and usable offline. The server kill switch is polled every ten minutes while the app runs; disabling it clears pending events and suspends collection without changing the user's preference.

No names, custom task text, error descriptions, image/audio content, embeddings, landmarks, or bounding boxes are encoded. Start events use a built-in task identifier or `custom`. Configuration includes effective detection thresholds, timing policy, buddy opt-in, speech/haptics/notifications, companion identifiers, experiment choices, front-camera mode and SFace model version. App, build, OS, device class and debug/test/production environment accompany each event. Configuration is immutable at start; later changes are separate events. Previously saved sessions are adopted with original configuration unavailable.

Identifiers: installation ID persists until opt-out/re-enable; app-run ID changes per process; session ID is the existing local session UUID; start-attempt ID links a blocked start through access approval. Session sequence is persisted transactionally and run index increases on continuation. Recognition epoch changes when session recognition memory resets. Existing buddy IDs are retained only as historical report identifiers, not recognition matches across process restarts.

## Events

| Family | Events / properties |
| --- | --- |
| App | `app.launch`, `app.foreground`, `app.inactive`, `app.background`, `app.usage`; foreground duration delta and cumulative total per process, screen, last observed interaction; temporary inactivity (such as a permission sheet) is distinct from backgrounding |
| UI | `ui.screen`, `ui.action`, `setup.viewed`, `setup.start_tapped`, `access.shown`, `access.result`, `permission.requested`, `permission.result`; structured actions, origin, result codes |
| Session | `session.started`, `session.adopted`, `session.configuration_changed`, `session.resumed`, `session.state`, `session.progress`, `session.outcome`, `session.timeline` |
| Capture | `camera.configuration`, `camera.start_attempt`, `camera.started`, `camera.recovered`, `camera.error`, `camera.window`; startup/first-verified-frame duration, evidence counts, processing duration sum/max, unknown/estimated/ambiguous participant observations, stale tracks |
| Recognition | `recognition.reset`, `recognition.error`; epoch, failure code, repeat count and degraded duration in camera windows |
| Speech | `voice.result`; completion, cancellation, missing-pack fallback, playback/decode failure |
| Delivery | `telemetry.dropped`; count of expired/evicted queue entries |

A session outcome is completed, cancelled, failed, or endedEarly. Failed/endedEarly sessions can continue under the same UUID. A cancelled/completed record cannot be reopened. Start Again creates a fresh session. Missing outcomes mean unknown/incomplete, never an inferred crash or abandonment.

State intervals split at checkpoints and transitions. `previous_state` owns each duration; `state` is the destination. Progress values are cumulative snapshots: select the latest sequence, do not sum them. Confirmed-present time advances the goal. Away and break overlap; never add them. Foreground usage is independent of session elapsed/present time. After process termination, the uncheckpointed tail is unknown (normally at most 15 seconds); never infer it from wall time. Relaunch/manual-break gaps retain existing conservative session accounting.

Timeline parts are immutable snapshots: snapshot ID, revision, zero-based part index, part count, and up to 50 intervals. Each outcome contains the full sanitized session history through that revision, participant visits, and recorded state intervals. The outcome references its expected snapshot ID and part count, so even a wholly missing timeline can be detected. Timeline completeness requires every unique part index. Do not sum timelines across revisions. Buddy intervals expose estimated seconds and attribution-measured seconds; older visits without these fields have unknown attribution quality. Overlapping people can produce more buddy time than shared session time. This telemetry does not measure actual recognition accuracy.

## Durability and delivery

SwiftData V6 adds `TelemetryRecord` while retaining V5 domain models. Session checkpoint, sequence/configuration metadata and outbox rows commit in one save; failure rolls back the transaction. State events are durably queued independently and a checkpoint follows them. Progress/foreground durations checkpoint every 15 seconds. Camera windows aggregate for 60 seconds; transitions and first errors enqueue immediately. Repeated errors aggregate in the next window. Upload runs every 60 seconds and at start/outcome/background when execution permits.

`POST /v1/telemetry/events`: 1–100 events, maximum 256 KiB. Envelope version 1 uses snake_case, UTC epoch occurrence timestamps, random event IDs and typed properties. Response returns `accepted`, `duplicates`, `rejected[{event_id,reason}]`, and `collection_enabled`. Only acknowledged IDs are removed; rejected events are quarantined. Retry uses exponential backoff/jitter; HTTP 429 respects numeric or HTTP-date Retry-After. No purchase token is sent. Existing XCorrelationId/request diagnostics apply.

The outbox/quarantine budget is 20 MiB or seven days. Routine samples are evicted before lifecycle events when the size limit is reached. Metadata and session history are not evicted by that budget. Delivery loss is represented by dropped counts; a failed disk cannot guarantee saving telemetry. Network failures never block session timing.

## Release and operations

1. Deploy backend migration `0008_telemetry`, then API routes, then the iOS build. No production deployment is performed by this change.
2. `STAYZY_TELEMETRY_ENABLED=false` activates the kill switch. `STAYZY_TELEMETRY_REQUESTS_PER_MINUTE` defaults to 120 per direct peer per worker. Configure trusted ingress/global rate limiting for multi-worker deployments; installation IDs are untrusted correlation, not authentication.
3. Raw events and session projections expire after 90 days. The API runs the idempotent purge at startup and daily. `python -m app.jobs.purge_telemetry` is also available for a scheduler; duplicate runs are safe.
4. Use `Docs/TELEMETRY_QUERIES.sql` in stayzyapi with an operator read-only database role. No public read endpoint or dashboard is added. Reports filter production traffic explicitly.
5. Monitor accepted/duplicate/rejected counts, event delivery lag, missing outcome/timeline parts, queue drops and purge failures. Investigate incomplete data rather than reporting it as zero usage.
6. Before release, verify physical-device camera permissions, process termination, interruptions, thermal pressure, sustained capture and analytics overhead. Review App Store privacy disclosures to reflect anonymous analytics. Simulator tests cannot validate real camera accuracy or thermal behavior.

The privacy manifest declares analytics identifiers, product interaction, performance and other diagnostics using [Apple’s data-collection manifest keys](https://developer.apple.com/documentation/technotes/tn3184-adding-data-collection-details-to-your-privacy-manifest). These are marked linked because events share a persistent installation identifier; cross-company tracking is false. No real-world identity or account is attached.
