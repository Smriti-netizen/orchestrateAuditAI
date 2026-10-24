"""Log evidence parser: key=value logs, pipe-delimited syslog, syslog prefixes, JSON-in-message."""
import json
import re

from .facts import ABSENT, PRESENT, Fact

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[Z]?)")
_KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
_SYSLOG = re.compile(r"^<\d+>\d*\s+\S+\s+\S+\s+\S+\s+\S+\s+")

_IDENT_KEYS = {
    # These keys hold either a stable internal ref (user_12) or an email, so
    # classify by value shape rather than trusting the key name.
    "customer_reference": "auto", "subject_reference": "auto",
    "subject_ref": "auto", "owner_key": "auto", "subject_key": "auto",
    "email": "email", "requester_email": "email", "customer_email": "email",
    "subject_email": "email", "owner_email": "email",
    "phone": "phone", "requester_phone": "phone", "owner_phone": "phone",
    "customer_phone": "phone", "email_hash": "email_hash",
    # value-shaped keys: classify by content
    "lookup_key": "auto", "match_key": "auto", "matched_value": "auto",
    "correlation_key": "auto",
}


_NULLISH = {"null", "none", "nil", "n/a", "na", "unknown", "-"}


def _classify_ident(value):
    v = str(value).strip()
    if "@" in v:
        return "email"
    if re.fullmatch(r"[0-9a-fA-F]{64}", v):
        return "email_hash"
    if re.fullmatch(r"[+\d][\d\s().-]{6,}", v):
        return "phone"
    return "user_ref"


def _collect_identifiers(fields):
    # Log exporters write the literal token ``null`` for an absent field.  Treated
    # as text it would look like an identifier (and would make an anonymised
    # analytics row look like it still exposed PII), so null-ish tokens are
    # dropped before classification.
    out = {}
    for key, kind in _IDENT_KEYS.items():
        raw = fields.get(key)
        if not raw or str(raw).strip().lower() in _NULLISH:
            continue
        kind = _classify_ident(raw) if kind == "auto" else kind
        out.setdefault(kind, str(raw).strip())
    return out


def _parse_line(line):
    line = line.strip()
    if not line:
        return None
    ts_m = _TS.match(line)
    ts = ts_m.group(1) if ts_m else ""
    body = line[ts_m.end():] if ts_m else line
    if body.startswith("<"):
        body = _SYSLOG.sub("", body)
    body = body.replace('\\"', '"')  # unescape JSON-style escaped quotes
    fields = {}
    event = None
    if body.lstrip().startswith("{"):
        try:
            obj = json.loads(body.lstrip())
            fields.update({str(k): str(v) for k, v in obj.items()})
        except Exception:
            fields = {}
    if not fields:
        for m in _KV.finditer(body):
            key, v1, v2, v3 = m.groups()
            val = v1 if v1 is not None else (v2 if v2 is not None else (v3 or ""))
            fields.setdefault(key, val)
    em = re.search(r"\bevent[\"']?[:=]\s*[\"']?([\w.]+)", body)
    event = fields.get("event") or em.group(1) if em else fields.get("event")
    fields.setdefault("event", event or "")
    fields.setdefault("level", "")
    fields.setdefault("service", "")
    fields.setdefault("trace_id", "")
    return ts, fields


def _presence(fields):
    ev = (fields.get("event") or "").lower()
    http = fields.get("http_status") or fields.get("status_code")
    rows = fields.get("rows")
    result = fields.get("result")
    available = fields.get("available")
    download = fields.get("download_available")
    if http and str(http).isdigit():
        return PRESENT if int(str(http)) < 400 else ABSENT
    if rows is not None and str(rows).isdigit():
        return PRESENT if int(str(rows)) > 0 else ABSENT
    if result:
        return PRESENT if str(result).lower() in ("hit", "found", "present", "ok", "true") else ABSENT
    if available is not None:
        return PRESENT if str(available).lower() in ("true", "yes", "1") else ABSENT
    if download is not None:
        return PRESENT if str(download).lower() in ("true", "yes", "1") else ABSENT
    if ev.endswith("_completed") or ev == "deletion_marker_check":
        return PRESENT
    if ev.endswith("_started"):
        return PRESENT
    return None


def parse_log_file(path, file_id, captured_at):
    """Returns (facts, events, errors). facts: Fact[]; events: raw log-event dicts."""
    facts, events, errors = [], [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            ts, fields = parsed
            event = fields.get("event") or ""
            if not event:
                continue
            resource_id = fields.get("resource_id") or ""
            system_id = fields.get("service") or fields.get("source") or "unknown"
            presence = _presence(fields)
            idents = _collect_identifiers(fields)
            f = None
            # *_started lines are telemetry, not a state assertion; they still
            # feed trace stitching below.
            if resource_id and not event.endswith("_started"):
                from .facts import SYSTEM_CATEGORY
                category = SYSTEM_CATEGORY.get(system_id, "UNKNOWN")
                # A contact-cache lookup is a secondary identifier store, not an
                # analytics profile or a retained conversation: policy requires
                # deleting the row rather than anonymising/redacting it.
                if ("contact_lookup" in event or "contact_query" in event) and \
                        resource_id.startswith(("analytics_", "support_")):
                    category = "CACHE_ENTRY"
                f = Fact(
                    file_id=file_id,
                    captured_at=ts or captured_at,
                    system_id=system_id,
                    resource_id=resource_id,
                    category=category,
                    presence=presence or PRESENT,
                    identifiers=idents,
                    extra={},
                    topology="primary",
                )
                f.extra["event"] = event
                f.extra["level"] = fields.get("level") or ""
                # A log line is an observation made at its own timestamp, not a
                # snapshot that only became knowable when the export file was
                # written: admissibility is judged on the event time.
                f.extra["event_stream"] = True
                f.extra["record_exists"] = (presence or PRESENT) == PRESENT
                # Restore eligibility / connector enablement from log fields.
                if "deletion_block" in fields:
                    f.extra["restore_allowed"] = str(fields.get("deletion_block")).lower() == "false"
                if "eligible" in fields and "restore" in event:
                    f.extra["restore_allowed"] = str(fields.get("eligible")).lower() == "true"
                if "enabled" in fields:
                    f.extra["enabled"] = str(fields.get("enabled")).lower() in ("true", "1", "yes")
                facts.append(f)
            events.append({
                "file_id": file_id, "captured_at": ts or captured_at, "event": event,
                "system_id": system_id, "resource_id": resource_id,
                "reported_result": fields.get("reported_result") or "",
                "details": fields.get("message") or fields.get("details") or "",
                "level": fields.get("level") or "",
                "identifiers": idents,
                "presence": presence or "",
                "trace_id": fields.get("trace_id") or "",
            })

    # Trace stitching: one request writes a *_started line carrying the lookup
    # identifier and a *_completed line carrying the resource id, linked only by
    # trace_id. Union identities across the same trace so the completed line can
    # be attributed to the subject (no timestamp guessing).
    trace_ids = {}
    by_trace = {}
    for ev in events:
        tid = ev.get("trace_id")
        if not tid:
            continue
        slot = by_trace.setdefault(tid, {"idents": {}, "resources": []})
        for k, v in (ev.get("identifiers") or {}).items():
            slot["idents"].setdefault(k, v)
        if ev.get("resource_id"):
            slot["resources"].append(ev["resource_id"])
    for f in facts:
        for ev in events:
            if ev.get("resource_id") == f.resource_id and ev.get("trace_id"):
                merged = by_trace.get(ev["trace_id"], {}).get("idents") or {}
                for k, v in merged.items():
                    f.identifiers.setdefault(k, v)
                f.extra["trace_id"] = ev["trace_id"]
                break
    return facts, events, errors

