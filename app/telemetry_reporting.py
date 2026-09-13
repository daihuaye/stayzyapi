"""Read-only reporting. SQL aggregates events; lifecycle order never uses receipt order."""
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import json

from fastapi import Query
from sqlalchemy import and_, case, func, literal, or_, select

from app.errors import api_error
from app.telemetry import TelemetryEvent as Event


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Window:
    def __init__(self, start: datetime | None = None, end: datetime | None = None,
                 as_of: datetime | None = None, environment: str = "production",
                 app_version: str | None = None):
        now = datetime.now(UTC)
        for value in (start, end, as_of):
            if value is not None and value.tzinfo is None:
                raise api_error(422, "telemetry_filter", "Timestamps must include a timezone.")
        self.as_of = utc(as_of or now)
        self.end = utc(end or self.as_of)
        self.start = utc(start or (self.end - timedelta(days=7)))
        if self.as_of > now + timedelta(seconds=5) or not timedelta(0) < self.end - self.start <= timedelta(days=90):
            raise api_error(422, "telemetry_filter", "Choose a positive date range of at most 90 days and a non-future snapshot.")
        if environment not in {"production", "debug", "test"}:
            raise api_error(422, "telemetry_filter", "Unsupported environment.")
        self.environment, self.app_version = environment, app_version

    def meta(self):
        return {"as_of": self.as_of, "start": self.start, "end": self.end, "environment": self.environment}

    def retained(self, e=Event):
        return [e.environment == self.environment, e.received_at <= self.as_of,
                e.received_at >= self.as_of - timedelta(days=90)]

    def period(self, e=Event):
        values = self.retained(e) + [e.occurred_at >= self.start, e.occurred_at < self.end]
        if self.app_version:
            values.append(e.payload["app_version"].as_string() == self.app_version)
        return values


def window(start: datetime | None = None, end: datetime | None = None,
           as_of: datetime | None = None, environment: str = "production",
           app_version: str | None = Query(None, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")):
    return Window(start, end, as_of, environment, app_version)


def num(p, key):
    return p[key].as_float()


def text(p, key):
    return p[key].as_string()


def flag(condition):
    return func.max(case((condition, 1), else_=0))


def count_if(condition):
    return func.sum(case((condition, 1), else_=0))


def session_query(w: Window, cohort=True):
    """One row per identity; ranking resolves duplicate sequences deterministically."""
    e = select(Event).where(*w.retained(), Event.session_id.is_not(None)).cte("retained")
    keys = [e.c.installation_id, e.c.session_id]
    p = e.c.payload["properties"]
    aggregate = select(*keys, func.max(e.c.occurred_at).label("last_activity"),
        func.max(e.c.received_at).label("last_received"), func.max(e.c.payload["run_index"].as_integer()).label("run_index"),
        func.sum(case((e.c.name.in_(["camera.error", "recognition.error"]), func.coalesce(num(p, "count"), 1)), else_=0)).label("error_count"),
        flag(e.c.name == "session.started").label("started"),
        flag(and_(e.c.name == "permission.result", text(p, "reason") == "authorized")).label("authorized"),
        flag(e.c.name == "camera.started").label("first_frame"),
        flag(and_(e.c.name == "session.state", text(p, "state") == "focused")).label("focused"),
        flag(and_(e.c.name == "permission.result", text(p, "reason").in_(["denied", "restricted"]))).label("denied")
    ).group_by(*keys).cte("session_groups")
    # Indexed identity lookups avoid joining large ranked CTEs (quadratic in SQLite).
    joins = []
    def latest_row(label, predicate, ascending=False):
        alias = Event.__table__.alias(label)
        chosen = select(Event.event_id).where(*w.retained(),
            Event.installation_id == aggregate.c.installation_id,
            Event.session_id == aggregate.c.session_id, predicate).order_by(
                Event.sequence.asc() if ascending else Event.sequence.desc(), Event.event_id).limit(1).correlate(aggregate).scalar_subquery()
        joins.append((alias, chosen))
        return alias
    first = latest_row("first", literal(True), True)
    start = latest_row("creation", Event.name.in_(["session.started", "session.adopted"]), True)
    latest = latest_row("lifecycle", Event.name.in_(["session.started", "session.adopted", "session.resumed", "session.state", "session.progress", "session.outcome"]))
    state = latest_row("state", Event.name == "session.state")
    progress = latest_row("progress", Event.name.in_(["session.progress", "session.outcome"]))
    joined = aggregate
    for alias, chosen in joins:
        joined = joined.outerjoin(alias, alias.c.event_id == chosen)
    lp = latest.c.payload["properties"]
    status = func.coalesce(text(lp, "status"), case(
        (latest.c.name == "session.state", case(*[(text(lp, "state") == a, b) for a, b in
            [("completed", "completed"), ("cancelled", "cancelled"), ("failed", "failed"), ("ended_early", "endedEarly")]], else_="inProgress")),
        else_="inProgress"))
    state_name = case((latest.c.name == "session.resumed", "relaunch_waiting"), else_=text(state.c.payload["properties"], "state"))
    created = func.coalesce(start.c.occurred_at, first.c.occurred_at)
    version = func.coalesce(start.c.payload["app_version"].as_string(), first.c.payload["app_version"].as_string())
    config = start.c.payload["properties"]["configuration"]
    q = select(aggregate, created.label("created_at"), version.label("app_version"),
        start.c.name.label("creation_event"), start.c.payload.label("start_context"), config.label("configuration"),
        config["tracks_participants"].as_boolean().label("buddy_tracking"),
        status.label("status"), state_name.label("state"), progress.c.payload["properties"].label("totals"),
        and_(status == "inProgress", func.coalesce(state_name, "unknown") != "manual_break",
             aggregate.c.last_activity <= w.as_of - timedelta(hours=24),
             aggregate.c.last_received <= w.as_of - timedelta(hours=24)).label("possible_drop_off")
    ).select_from(joined)
    if cohort:
        q = q.where(created >= w.start, created < w.end)
    if w.app_version and cohort:
        q = q.where(version == w.app_version)
    return q.cte("sessions")


def session_dict(row):
    d = dict(row)
    d["configuration_available"] = d.pop("creation_event") == "session.started" and d["configuration"] is not None
    if not d["configuration_available"]:
        d["configuration"] = None
    context = d.pop("start_context") or {}
    d["start_metadata"] = {k: context[k] for k in ("app_version", "app_build", "os_version", "device_class", "app_run_id", "start_attempt_id") if context.get(k) is not None}
    d["start_metadata"].update({k: context.get("properties", {}).get(k) for k in ("task", "origin") if context.get("properties", {}).get(k) is not None})
    d["run_count"] = (d.pop("run_index") or 0) + 1
    d["totals"] = d["totals"] or {}
    d["possible_drop_off"] = bool(d["possible_drop_off"])
    return d


def encode_cursor(values, scope):
    return base64.urlsafe_b64encode(json.dumps({"values": values, "scope": scope}, default=str).encode()).decode()


def decode_cursor(value, scope):
    try:
        obj = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if obj["scope"] != scope or not isinstance(obj["values"], list):
            raise ValueError()
        return obj["values"]
    except (ValueError, KeyError, TypeError):
        raise api_error(422, "telemetry_cursor", "Invalid cursor or changed filters. Refresh the report.")


def scope_for(w, extra):
    return hashlib.sha256(json.dumps([w.meta(), w.app_version, extra], default=str, sort_keys=True).encode()).hexdigest()


async def rows(db, query):
    return [{k: utc(v) if isinstance(v, datetime) else v for k, v in r.items()}
            for r in (await db.execute(query)).mappings()]


def bucket(db, value):
    return func.date_trunc("day", func.timezone("UTC", value)) if db.bind.dialect.name == "postgresql" else func.date(value)


async def overview(db, w):
    e, p = Event, Event.payload["properties"]
    s = session_query(w)
    summary = (await rows(db, select(func.count().label("sessions_observed"), func.coalesce(func.sum(s.c.started), 0).label("sessions_started"),
        count_if(s.c.status == "completed").label("completed"), count_if(s.c.status == "failed").label("failed"),
        count_if(s.c.status == "inProgress").label("incomplete"), count_if(s.c.possible_drop_off).label("possible_drop_offs")).select_from(s)))[0]
    usage = (await rows(db, select(func.count(func.distinct(e.installation_id)).label("active_installations"),
        func.sum(case((e.name == "app.usage", num(p, "duration")), else_=None)).label("foreground_seconds")).where(*w.period(), e.name.like("app.%"))))[0]
    day = bucket(db, e.occurred_at)
    daily = await rows(db, select(day.label("day"), func.count(func.distinct(e.installation_id)).label("installations"),
        func.sum(case((e.name == "app.usage", num(p, "duration")), else_=None)).label("foreground_seconds"),
        count_if(and_(e.name == "session.outcome", text(p, "status") == "completed")).label("completions"),
        count_if(and_(e.name == "session.outcome", text(p, "status") == "failed")).label("failures")
    ).where(*w.period(), or_(e.name.like("app.%"), e.name == "session.outcome")).group_by(day).order_by(day))
    screens = await rows(db, select(text(p, "screen").label("label"), func.sum(num(p, "duration")).label("value")).where(*w.period(), e.name == "app.usage").group_by(text(p, "screen")))
    outcomes = await rows(db, select(s.c.status.label("label"), func.count().label("value"), func.avg(num(s.c.totals, "progress")).label("mean_progress")).group_by(s.c.status))
    waits = await rows(db, select(text(p, "previous_state").label("label"), func.sum(num(p, "duration")).label("value"))
        .where(*w.period(), e.name == "session.state", text(p, "previous_state").in_(["permission_waiting", "camera_starting", "acquiring", "warming_up", "recovering", "manual_break", "relaunch_waiting"]))
        .group_by(text(p, "previous_state")).order_by(func.sum(num(p, "duration")).desc()))
    # Attempts form a separate cohort; access gates are branches, not required stages.
    a = e.payload["start_attempt_id"].as_string()
    cohort = select(e.installation_id.label("installation_id"), a.label("attempt_id")).where(*w.period(), e.name == "setup.start_tapped", a.is_not(None)).distinct().cte("attempts")
    attempts = select(cohort, flag(e.name == "session.started").label("created"), flag(e.name == "access.shown").label("gated")).select_from(cohort.outerjoin(e, and_(e.installation_id == cohort.c.installation_id, a == cohort.c.attempt_id, *w.retained(), e.name.in_(["session.started", "access.shown"])))).group_by(cohort.c.installation_id, cohort.c.attempt_id).cte("attempt_results")
    attempt_totals = (await rows(db, select(func.count().label("tapped"), func.sum(attempts.c.created).label("created"), func.sum(attempts.c.gated).label("gated")).select_from(attempts)))[0]
    conditions = [s.c.started == 1]
    funnel = []
    for key in ("started", "authorized", "first_frame", "focused"):
        conditions.append(s.c[key] == 1)
        funnel.append({"label": key, "value": await db.scalar(select(func.count()).select_from(s).where(*conditions))})
    funnel.append({"label": "completed", "value": await db.scalar(select(func.count()).select_from(s).where(*conditions, s.c.status == "completed"))})
    denied = await db.scalar(select(func.count()).select_from(s).where(s.c.started == 1, s.c.denied == 1))
    setup_views = await db.scalar(select(func.count()).select_from(e).where(*w.period(), e.name == "setup.viewed"))
    quality = (await rows(db, select(func.count().label("events"),
        func.sum(case((e.name == "telemetry.dropped", num(p, "count")), else_=0)).label("dropped_events")).where(*w.period())))[0]
    lag = (func.extract("epoch", e.received_at) - func.extract("epoch", e.occurred_at)) if db.bind.dialect.name == "postgresql" else (func.julianday(e.received_at) - func.julianday(e.occurred_at)) * 86400
    quality.update((await rows(db, select(func.avg(case((lag >= 0, lag), else_=None)).label("mean_delivery_lag_seconds"),
        func.max(case((lag >= 0, lag), else_=None)).label("max_delivery_lag_seconds"), count_if(lag < 0).label("clock_skew_events")).where(*w.period())))[0])
    # Expected snapshots originate in outcome references, including zero delivered parts.
    outcome_rank = select(e.installation_id, e.session_id, p["snapshot_id"].as_string().label("snapshot_id"),
        p["revision"].as_integer().label("revision"), p["part_count"].as_integer().label("expected_parts"),
        func.row_number().over(partition_by=[e.installation_id, e.session_id], order_by=[e.sequence.desc(), e.event_id]).label("rn")
    ).where(*w.retained(), e.name == "session.outcome").cte("outcome_references")
    parts = select(e.installation_id, e.session_id, p["snapshot_id"].as_string().label("snapshot_id"),
        p["revision"].as_integer().label("revision"), func.count(func.distinct(p["part_index"].as_integer())).label("received_parts")
    ).where(*w.retained(), e.name == "session.timeline").group_by(e.installation_id, e.session_id, p["snapshot_id"].as_string(), p["revision"].as_integer()).cte("parts")
    o = outcome_rank.c
    coverage = outcome_rank.join(s, and_(s.c.installation_id == o.installation_id, s.c.session_id == o.session_id)).outerjoin(parts,
        and_(parts.c.installation_id == o.installation_id, parts.c.session_id == o.session_id, parts.c.snapshot_id == o.snapshot_id, parts.c.revision == o.revision))
    quality.update((await rows(db, select(func.count().label("expected_snapshots"),
        func.coalesce(count_if(or_(o.expected_parts.is_(None), func.coalesce(parts.c.received_parts, 0) < o.expected_parts)), 0).label("incomplete_snapshots"))
        .select_from(coverage).where(o.rn == 1, o.snapshot_id.is_not(None))))[0])
    versions = (await db.scalars(select(e.payload["app_version"].as_string()).where(*w.retained()).distinct().order_by(e.payload["app_version"].as_string()).limit(200))).all()
    return {**w.meta(), "summary": {**summary, **usage}, "daily": daily, "screens": screens,
            "outcomes": outcomes, "waits": waits, "attempts": attempt_totals, "funnel": funnel,
            "permission_denials": denied, "setup_views": setup_views, "quality": quality, "app_versions": versions}


async def health(db, w):
    e, p = Event, Event.payload["properties"]
    def metrics():
        win = e.name == "camera.window"
        def total(key):
            return func.sum(case((win, num(p, key)), else_=None)).label(key)
        return [count_if(e.name == "camera.start_attempt").label("attempts"), count_if(e.name == "camera.started").label("first_frames"),
            func.avg(case((e.name == "camera.started", num(p, "duration")), else_=None)).label("startup_mean_seconds"),
            func.max(case((e.name == "camera.started", num(p, "duration")), else_=None)).label("startup_max_seconds"),
            count_if(e.name == "camera.recovered").label("recoveries"),
            *[total(k) for k in ["samples", "processing_ms", "degraded_seconds", "missing_samples", "body_samples", "face_samples", "ambiguous_samples", "stale_tracks", "known_seconds", "unknown_seconds", "estimated_seconds", "attribution_measured_seconds"]],
            func.max(case((win, num(p, "max_processing_ms")), else_=None)).label("max_processing_ms")]
    def finalize(r):
        r["mean_processing_ms"] = r["processing_ms"] / r["samples"] if r["samples"] else None
        return r
    summary = finalize((await rows(db, select(*metrics()).where(*w.period())))[0])
    cohort = session_query(w)
    attribution = (await rows(db, select(*[func.sum(num(cohort.c.totals, k)).label(k) for k in
        ["known_seconds", "unknown_seconds", "estimated_seconds", "attribution_measured_seconds"]],
        func.count(num(cohort.c.totals, "known_seconds")).label("attribution_sessions")).select_from(cohort)))[0]
    summary.update(attribution)
    day = bucket(db, e.occurred_at)
    daily = [finalize(r) for r in await rows(db, select(day.label("day"), *metrics()).where(*w.period(), e.name.like("camera.%")).group_by(day).order_by(day))]
    v = e.payload["app_version"].as_string()
    versions = [finalize(r) for r in await rows(db, select(v.label("app_version"), *metrics()).where(*w.period()).group_by(v).order_by(v))]
    errors = await rows(db, select(e.name.label("name"), text(p, "reason").label("reason"), func.sum(func.coalesce(num(p, "count"), 1)).label("count"))
        .where(*w.period(), e.name.in_(["camera.error", "recognition.error"])).group_by(e.name, text(p, "reason")).order_by(func.sum(func.coalesce(num(p, "count"), 1)).desc()))
    diagnostics = await rows(db, select(e.installation_id, e.session_id, e.occurred_at, e.name, text(p, "reason").label("reason"))
        .where(*w.period(), e.name.in_(["camera.error", "recognition.error", "camera.recovered"])).order_by(e.occurred_at.desc(), e.event_id).limit(25))
    return {**w.meta(), "summary": summary, "daily": daily, "versions": versions, "errors": errors, "diagnostics": diagnostics}


async def snapshots(db, w, identities, include_intervals=False):
    if not identities:
        return {}
    e = Event
    match = or_(*[and_(e.installation_id == i, e.session_id == s) for i, s in identities])
    p = e.payload["properties"]
    fields = ["snapshot_id", "revision", "part_count", "part_index"]
    projection = [p[k].as_string().label(k) if k == "snapshot_id" else p[k].as_integer().label(k) for k in fields]
    if include_intervals:
        projection.append(p["intervals"].label("intervals"))
    metadata = await rows(db, select(e.installation_id, e.session_id, e.sequence, e.name, *projection).where(
        *w.retained(), match, e.name.in_(["session.outcome", "session.timeline"])).order_by(e.sequence, e.event_id))
    groups = {}
    for row in metadata:
        key = (row["installation_id"], row["session_id"])
        p = {k: row[k] for k in fields}
        if include_intervals:
            p["intervals"] = row["intervals"] or []
        if not p.get("snapshot_id"):
            continue
        sid = (p["snapshot_id"], (p.get("revision") or 0))
        group = groups.setdefault(key, {}).setdefault(sid, {"snapshot_id": sid[0], "revision": sid[1], "expected_parts": (p.get("part_count") or 0), "parts": {}, "outcome_sequence": None})
        if row["name"] == "session.outcome":
            group["outcome_sequence"] = row["sequence"]
            group["expected_parts"] = (p.get("part_count") or 0)
        else:
            group["parts"][p["part_index"]] = p if include_intervals else {}
    result = {}
    for key, collection in groups.items():
        output = []
        for group in collection.values():
            expected = group["expected_parts"]
            parts = group.pop("parts")
            group["received_parts"] = len([x for x in parts if 0 <= x < expected])
            group["complete"] = expected > 0 and all(x in parts for x in range(expected))
            if include_intervals:
                intervals = {}
                for index in sorted(parts):
                    if index < expected:
                        for interval in parts[index].get("intervals", []):
                            intervals[interval["id"]] = interval
                group["intervals"] = sorted(intervals.values(), key=lambda i: (i["started_at"], i["id"]))
            output.append(group)
        result[key] = sorted(output, key=lambda g: (g["outcome_sequence"] or -1, g["revision"]), reverse=True)
    return result


async def sessions(db, w, limit=50, cursor=None, session_id=None, status=None, state=None,
                   buddy=None, errors=None, possible_drop_off=None, stage=None, reason=None, wait_state=None, activity=False):
    s = session_query(w, cohort=not activity)
    q = select(s)
    filters = dict(session_id=session_id, status=status, state=state, buddy=buddy, errors=errors,
                   possible_drop_off=possible_drop_off, stage=stage, reason=reason, wait_state=wait_state, activity=activity)
    for column, value in [(s.c.session_id, session_id), (s.c.status, status), (s.c.state, state),
                          (s.c.buddy_tracking, buddy), (s.c.possible_drop_off, possible_drop_off)]:
        if value is not None:
            q = q.where(column == value)
    if errors is not None:
        q = q.where(s.c.error_count > 0 if errors else s.c.error_count == 0)
    if stage:
        valid = {"started", "authorized", "first_frame", "focused", "completed", "denied"}
        if stage not in valid:
            raise api_error(422, "telemetry_filter", "Unsupported funnel stage.")
        conditions = [s.c.started == 1]
        if stage == "denied":
            conditions.append(s.c.denied == 1)
        else:
            for key in ("started", "authorized", "first_frame", "focused", "completed"):
                conditions.append(s.c.status == "completed" if key == "completed" else s.c[key] == 1)
                if key == stage:
                    break
        q = q.where(*conditions)
    if activity:
        q = q.where(select(Event.event_id).where(*w.period(), Event.installation_id == s.c.installation_id,
            Event.session_id == s.c.session_id).exists())
    if wait_state:
        q = q.where(select(Event.event_id).where(*w.period(), Event.installation_id == s.c.installation_id,
            Event.session_id == s.c.session_id, Event.name == "session.state",
            text(Event.payload["properties"], "previous_state") == wait_state,
            num(Event.payload["properties"], "duration") > 0).exists())
    if reason:
        q = q.where(select(Event.event_id).where(*w.period(), Event.installation_id == s.c.installation_id,
            Event.session_id == s.c.session_id, text(Event.payload["properties"], "reason") == reason).exists())
    scope = scope_for(w, filters)
    if cursor:
        try:
            activity, installation, session = decode_cursor(cursor, scope)
            activity = datetime.fromisoformat(activity)
            q = q.where(or_(s.c.last_activity < activity,
                and_(s.c.last_activity == activity, s.c.installation_id > installation),
                and_(s.c.last_activity == activity, s.c.installation_id == installation, s.c.session_id > session)))
        except (ValueError, TypeError):
            raise api_error(422, "telemetry_cursor", "Invalid session cursor.")
    data = await rows(db, q.order_by(s.c.last_activity.desc(), s.c.installation_id, s.c.session_id).limit(limit + 1))
    more = len(data) > limit
    data = data[:limit]
    timeline = await snapshots(db, w, [(r["installation_id"], r["session_id"]) for r in data])
    output = []
    for r in data:
        value = session_dict(r)
        outcomes = [x for x in timeline.get((r["installation_id"], r["session_id"]), []) if x["outcome_sequence"] is not None]
        value["timeline"] = outcomes[0] if outcomes else None
        output.append(value)
    last = data[-1] if more else None
    return {**w.meta(), "items": output, "next_cursor": encode_cursor([last["last_activity"], last["installation_id"], last["session_id"]], scope) if last else None}


async def session_detail(db, w, installation_id, session_id):
    # Detail intentionally ignores cohort dates and version: show the full retained identity.
    detail_window = Window(as_of=w.as_of, environment=w.environment)
    s = session_query(detail_window, cohort=False)
    found = await rows(db, select(s).where(s.c.installation_id == installation_id, s.c.session_id == session_id))
    if not found:
        raise api_error(404, "telemetry_session_not_found", "No retained session exists for this identity and environment.")
    value = session_dict(found[0])
    e = Event
    result = await db.stream_scalars(select(e).where(*w.retained(), e.installation_id == installation_id,
        e.session_id == session_id, e.name != "session.timeline").order_by(e.sequence, e.event_id))
    configuration_changes, outcomes, epochs, state_intervals, checkpoints, markers = [], [], {}, [], [], []
    raw_states = {}
    async for event in result:
        p = event.payload["properties"]
        at = utc(event.occurred_at).timestamp()
        record = {"event_id": event.event_id, "sequence": event.sequence, "occurred_at": utc(event.occurred_at),
                  "name": event.name, "run_index": event.payload.get("run_index", 0), "properties": p}
        if event.name == "session.configuration_changed":
            configuration_changes.append(record)
        if event.name == "session.outcome":
            outcomes.append(record)
        if event.name in {"session.progress", "session.outcome"}:
            checkpoints.append({"at": at, "progress": p.get("progress"), "present_seconds": p.get("present_seconds"), "sequence": event.sequence})
        if event.name == "session.state" and p.get("duration") is not None:
            interval = {"id": event.event_id, "kind": p.get("previous_state", "unknown"), "started_at": at - p["duration"], "ended_at": at, "duration": p["duration"], "lane": "state"}
            raw_states[event.event_id] = interval
            if state_intervals and state_intervals[-1]["kind"] == interval["kind"] and abs(state_intervals[-1]["ended_at"] - interval["started_at"]) < 0.05:
                state_intervals[-1]["ended_at"] = at
                state_intervals[-1]["duration"] += interval["duration"]
            else:
                state_intervals.append(dict(interval))
        if event.name in {"camera.error", "camera.recovered", "recognition.error", "recognition.reset", "session.resumed", "session.outcome", "camera.started", "permission.result"}:
            markers.append(record)
        epoch = p.get("recognition_epoch_id")
        if epoch:
            entry = epochs.setdefault(epoch, {"recognition_epoch_id": epoch, "known_seconds": None, "unknown_seconds": None, "estimated_seconds": None, "attribution_measured_seconds": None, "degraded_seconds": None})
            if event.name == "camera.window":
                for key in ["degraded_seconds"]:
                    if p.get(key) is not None:
                        entry[key] = (entry[key] or 0) + p[key]
    timeline = (await snapshots(db, w, [(installation_id, session_id)], True)).get((installation_id, session_id), [])
    referenced = [x for x in timeline if x["outcome_sequence"] is not None]
    selected = referenced[0] if referenced else None
    if selected:
        domain_kinds = {"uncertainDetection", "detectedAway", "manualBreak", "appInactive", "cameraInterrupted", "cameraUnavailable", "detectorFailure", "cameraPermissionAttempt", "cameraRestartAttempt", "sessionFailed", "recovery", "relaunchGap", "endedEarly", "cancelled", "completed"}
        for interval in selected.get("intervals", []):
            if interval["id"] in raw_states or interval["kind"] not in domain_kinds | {"buddy_visit", "unknown_visit"}:
                interval["lane"] = "state"
                raw_states[interval["id"]] = interval
            if interval["kind"] in {"buddy_visit", "unknown_visit"}:
                epoch = interval.get("recognition_epoch_id") or "unavailable"
                entry = epochs.setdefault(epoch, {"recognition_epoch_id": epoch, "known_seconds": None, "unknown_seconds": None, "estimated_seconds": None, "attribution_measured_seconds": None, "degraded_seconds": None})
                key = "known_seconds" if interval["kind"] == "buddy_visit" else "unknown_seconds"
                entry[key] = (entry[key] or 0) + interval["duration"]
                for key in ("estimated_seconds", "attribution_measured_seconds"):
                    if interval.get(key) is not None:
                        entry[key] = (entry[key] or 0) + interval[key]
        # Prefer snapshot copies by interval ID, while retaining later live transitions.
        state_intervals = []
        for interval in sorted(raw_states.values(), key=lambda i: (i["started_at"], i["id"])):
            interval = {**interval, "ended_at": interval.get("ended_at") or interval["started_at"] + interval["duration"]}
            if state_intervals and state_intervals[-1]["kind"] == interval["kind"] and abs(state_intervals[-1]["ended_at"] - interval["started_at"]) < .05:
                state_intervals[-1]["ended_at"] = interval["ended_at"]
                state_intervals[-1]["duration"] += interval["duration"]
            else:
                state_intervals.append(dict(interval))
    # Send only the selected snapshot's intervals; earlier snapshots remain inspectable in the event log.
    for snapshot in timeline:
        if snapshot is not selected:
            snapshot.pop("intervals", None)
    checkpoint_count = len(checkpoints)
    if checkpoint_count > 500:
        checkpoints = [checkpoints[round(i * (checkpoint_count - 1) / 499)] for i in range(500)]
    marker_count = len(markers)
    return {**w.meta(), "session": value, "configuration_changes": configuration_changes,
            "outcomes": outcomes, "epochs": list(epochs.values()), "state_intervals": state_intervals,
            "checkpoints": checkpoints, "checkpoint_count": checkpoint_count,
            "markers": markers[-500:], "marker_count": marker_count,
            "snapshots": timeline, "selected_snapshot": selected}


async def session_events(db, w, installation_id, session_id, limit=50, cursor=None):
    e = Event
    scope = scope_for(w, [installation_id, session_id, "events"])
    q = select(e).where(*w.retained(), e.installation_id == installation_id, e.session_id == session_id)
    if cursor:
        try:
            seq, eid = decode_cursor(cursor, scope)
            if not isinstance(seq, int) or not isinstance(eid, str):
                raise ValueError()
            q = q.where(or_(e.sequence > seq, and_(e.sequence == seq, e.event_id > eid)))
        except (ValueError, TypeError):
            raise api_error(422, "telemetry_cursor", "Invalid event cursor.")
    events = list((await db.scalars(q.order_by(e.sequence, e.event_id).limit(limit + 1))).all())
    more = len(events) > limit
    events = events[:limit]
    return {**w.meta(), "items": [{**e.payload, "received_at": utc(e.received_at)} for e in events],
            "next_cursor": encode_cursor([events[-1].sequence, events[-1].event_id], scope) if more else None}
