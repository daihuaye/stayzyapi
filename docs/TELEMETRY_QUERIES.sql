-- PostgreSQL, operator read-only access. Every query excludes debug/test traffic.
-- Timestamps are client occurrence time; received_at measures delivery lag.
-- Never sum cumulative progress/foreground_seconds, or overlapping Away and Break.

-- Daily usage by screen and app version; duration is a foreground delta.
SELECT date_trunc('day', occurred_at) AS day,
       payload->>'app_version' AS app_version,
       payload->'properties'->>'screen' AS screen,
       count(DISTINCT installation_id) AS active_installations,
       sum((payload->'properties'->>'duration')::double precision) AS foreground_seconds
FROM telemetry_events
WHERE environment = 'production' AND name = 'app.usage'
GROUP BY 1, 2, 3 ORDER BY 1 DESC;

-- Start-attempt funnel. A session may take multiple camera attempts.
WITH attempts AS (
 SELECT installation_id, payload->>'start_attempt_id' AS attempt_id,
        bool_or(name = 'setup.start_tapped') AS tapped,
        bool_or(name = 'access.shown') AS access_shown,
        bool_or(name = 'session.started') AS started
 FROM telemetry_events WHERE environment = 'production'
 AND payload->>'start_attempt_id' IS NOT NULL
 GROUP BY 1,2
)
SELECT count(*) FILTER (WHERE tapped) AS start_taps,
       count(*) FILTER (WHERE access_shown) AS gated,
       count(*) FILTER (WHERE started) AS sessions_created
FROM attempts;

WITH sessions AS (
 SELECT installation_id, session_id,
   bool_or(name = 'session.started') AS started,
   bool_or(name = 'permission.result' AND payload->'properties'->>'reason' = 'authorized') AS permission_granted,
   bool_or(name = 'camera.started') AS first_verified_frame,
   bool_or(name = 'session.state' AND payload->'properties'->>'state' = 'focused') AS focused,
   bool_or(name = 'session.outcome' AND payload->'properties'->>'status' = 'completed') AS completed
 FROM telemetry_events WHERE environment = 'production' AND session_id IS NOT NULL GROUP BY 1,2
)
SELECT count(*) FILTER (WHERE started) AS started,
       count(*) FILTER (WHERE permission_granted) AS permission_granted,
       count(*) FILTER (WHERE first_verified_frame) AS camera_started,
       count(*) FILTER (WHERE focused) AS focused,
       count(*) FILTER (WHERE completed) AS completed
FROM sessions;

-- State dwell; long dwell is a diagnostic clue, not proof of being stuck.
SELECT payload->'properties'->>'previous_state' AS state,
       count(DISTINCT (installation_id, session_id)) AS sessions,
       sum((payload->'properties'->>'duration')::double precision) AS seconds
FROM telemetry_events WHERE environment = 'production' AND name = 'session.state'
GROUP BY 1 ORDER BY seconds DESC;

-- Latest outcome/progress by immutable start configuration. Old adopted sessions
-- have no original configuration. inProgress includes incomplete/unknown sessions.
WITH starts AS (
 SELECT DISTINCT ON (installation_id, session_id) installation_id, session_id,
        payload->'properties'->'configuration' AS configuration
 FROM telemetry_events WHERE environment = 'production' AND name = 'session.started'
 ORDER BY installation_id, session_id, sequence
)
SELECT s.summary->'properties'->>'status' AS status,
       st.configuration->>'tracks_participants' AS buddy_tracking,
       st.configuration->>'target_seconds' AS target_seconds,
       count(*) AS sessions,
       avg((s.summary->'properties'->>'progress')::double precision) AS mean_progress
FROM telemetry_sessions s LEFT JOIN starts st USING (installation_id, session_id)
WHERE s.summary->>'environment' = 'production' GROUP BY 1,2,3;

-- Camera windows use processing duration SUM and sample count, not percentiles.
-- Startup latency does have one duration per successful attempt.
SELECT payload->>'app_version' AS version,
       sum((payload->'properties'->>'processing_ms')::double precision) /
         nullif(sum((payload->'properties'->>'samples')::double precision), 0) AS mean_processing_ms,
       max((payload->'properties'->>'max_processing_ms')::double precision) AS max_processing_ms,
       sum((payload->'properties'->>'degraded_seconds')::double precision) AS recognition_degraded_seconds,
       sum((payload->'properties'->>'missing_samples')::double precision) AS missing_samples
FROM telemetry_events WHERE environment = 'production' AND name = 'camera.window' GROUP BY 1;
SELECT name, payload->'properties'->>'reason' AS reason,
       sum(coalesce((payload->'properties'->>'count')::double precision, 1)) AS occurrences
FROM telemetry_events WHERE environment = 'production'
AND name IN ('camera.error','recognition.error','camera.recovered','camera.start_attempt') GROUP BY 1,2;
SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY (payload->'properties'->>'duration')::double precision) AS startup_p95_seconds
FROM telemetry_events WHERE environment = 'production' AND name = 'camera.started';

-- Timeline completeness. Keep snapshot revisions separate and count distinct parts.
SELECT installation_id, session_id, payload->'properties'->>'snapshot_id' AS snapshot_id,
       max((payload->'properties'->>'revision')::integer) AS revision,
       count(DISTINCT payload->'properties'->>'part_index') AS received_parts,
       max((payload->'properties'->>'part_count')::integer) AS expected_parts,
       count(DISTINCT payload->'properties'->>'part_index') = max((payload->'properties'->>'part_count')::integer) AS complete
FROM telemetry_events WHERE environment = 'production' AND name = 'session.timeline' GROUP BY 1,2,3;

-- Buddy attribution by epoch, using ONLY the newest complete timeline per session.
WITH parts AS (
 SELECT *, payload->'properties' AS p FROM telemetry_events
 WHERE environment = 'production' AND name = 'session.timeline'
), complete AS (
 SELECT installation_id, session_id, p->>'snapshot_id' AS snapshot_id,
        max((p->>'revision')::integer) AS revision
 FROM parts GROUP BY 1,2,3
 HAVING count(DISTINCT p->>'part_index') = max((p->>'part_count')::integer)
), latest AS (
 SELECT DISTINCT ON (installation_id, session_id) * FROM complete
 ORDER BY installation_id, session_id, revision DESC
), visits AS (
 SELECT parts.installation_id, parts.session_id, item
 FROM parts JOIN latest ON parts.installation_id = latest.installation_id
 AND parts.session_id = latest.session_id AND parts.p->>'snapshot_id' = latest.snapshot_id
 CROSS JOIN LATERAL json_array_elements(parts.p->'intervals') AS item
)
SELECT installation_id, session_id, item->>'recognition_epoch_id' AS epoch,
       sum((item->>'duration')::double precision) FILTER (WHERE item->>'kind' = 'buddy_visit') AS attributed_seconds,
       sum((item->>'duration')::double precision) FILTER (WHERE item->>'kind' = 'unknown_visit') AS unknown_seconds,
       sum((item->>'estimated_seconds')::double precision) AS estimated_seconds,
       sum((item->>'attribution_measured_seconds')::double precision) AS measured_seconds
FROM visits WHERE item->>'kind' IN ('buddy_visit','unknown_visit') GROUP BY 1,2,3;

-- Delivery lag and dropped samples; a missing final upload is not a known crash.
SELECT name, count(*) AS events,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - occurred_at)) AS delivery_p95_seconds
FROM telemetry_events WHERE environment = 'production' GROUP BY 1;
SELECT sum((payload->'properties'->>'count')::double precision) AS dropped
FROM telemetry_events WHERE environment = 'production' AND name = 'telemetry.dropped';

-- Session reconstruction: bind :session_id and :installation_id in your SQL client.
-- SELECT sequence, name, occurred_at, received_at, payload FROM telemetry_events
-- WHERE session_id = :session_id AND installation_id = :installation_id
-- ORDER BY sequence, event_id;

-- Entirely missing timeline uploads, not just partial uploads.
WITH delivered AS (
 SELECT installation_id, session_id, payload->'properties'->>'snapshot_id' AS snapshot_id,
        count(DISTINCT payload->'properties'->>'part_index') AS parts
 FROM telemetry_events WHERE environment = 'production' AND name = 'session.timeline'
 GROUP BY 1,2,3
)
SELECT s.installation_id, s.session_id,
       s.summary->'properties'->>'snapshot_id' AS expected_snapshot,
       coalesce(d.parts, 0) AS delivered_parts,
       (s.summary->'properties'->>'part_count')::integer AS expected_parts
FROM telemetry_sessions s LEFT JOIN delivered d ON s.installation_id = d.installation_id
AND s.session_id = d.session_id AND s.summary->'properties'->>'snapshot_id' = d.snapshot_id
WHERE s.summary->>'environment' = 'production'
AND s.summary->'properties'->>'snapshot_id' IS NOT NULL
AND coalesce(d.parts, 0) < (s.summary->'properties'->>'part_count')::integer;
