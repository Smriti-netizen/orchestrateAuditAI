"""SQLite evidence parser: loads a .sql snapshot into an in-memory DB and emits Facts."""
import re
import sqlite3

from .facts import ABSENT, PRESENT, Fact, row_category, row_system


def parse_sql_file(path, file_id, captured_at):
    facts = []
    errors = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    try:
        con = sqlite3.connect(":memory:")
        con.executescript(text)
    except Exception as exc:
        # Fall back to line-by-line execution for pragma/comment quirks.  Every
        # skipped statement is recorded: a partially loaded dump still claims
        # ``covers`` on the rows it did load, so silent failures would turn a
        # broken snapshot into false absence evidence.
        errors.append({"stage": "executescript",
                       "error": f"{type(exc).__name__}: {exc}"})
        con = sqlite3.connect(":memory:")
        for number, stmt in enumerate([s.strip() for s in text.split(";") if s.strip()], 1):
            try:
                con.execute(stmt)
            except Exception as inner:
                errors.append({"stage": "fallback", "statement": number,
                               "error": f"{type(inner).__name__}: {inner}"})

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    tables = [t for t in tables if t != "snapshot_scope"]

    for t in tables:
        colnames = [r[1] for r in con.execute(f"PRAGMA table_info({t})").fetchall()]
        lower_map = {c.lower(): c for c in colnames}
        rows = con.execute(f"SELECT * FROM {t}").fetchall()
        for row in rows:
            rec = dict(zip(colnames, row))
            # Some snapshots store the record as a JSON blob plus origin metadata.
            blob = rec.get("record_json") or rec.get("payload_json")
            system_hint, collection = None, None
            if blob:
                import json as _jsonlib
                try:
                    inner = _jsonlib.loads(blob)
                except Exception:
                    inner = {}
                system_hint = rec.get("origin_system")
                collection = rec.get("collection_name")
                rec = {k: v for k, v in rec.items()
                       if k not in ("record_json", "payload_json")}
                rec.update(inner)
            fact = _row_to_fact(t, rec, file_id, captured_at,
                                system_hint=system_hint, collection=collection)
            if fact is not None:
                fact.covers = True  # a table dump enumerates the whole collection
                fact.extra["collection"] = collection or t
                facts.append(fact)

    return facts, errors


def _pick(rec, names):
    for n in names:
        for k, v in rec.items():
            if k.lower() == n.lower():
                return v
    return None


def _row_to_fact(table, rec, file_id, captured_at, system_hint=None, collection=None):
    # Search rows nest the identifying fields under "document".
    doc = rec.get("document") if isinstance(rec.get("document"), dict) else {}
    if doc:
        merged = dict(doc)
        merged.update({k: v for k, v in rec.items() if v is not None})
        rec = merged
    resource_id = _pick(rec, ["resource_id", "doc_id", "archive_key", "object_key"]) or ""
    # A row's own system / record_type (an ``origin_system`` column, or a
    # ``system`` field inside ``record_json``) outranks the table-name guess.
    system_id = row_system(rec) or system_hint or _infer_system(table)
    if system_hint:
        from .facts import SYSTEM_CATEGORY
        category = (row_category(rec) or SYSTEM_CATEGORY.get(system_hint)
                    or _infer_category(collection or table))
    else:
        category = row_category(rec) or _infer_category(collection or table)
    topology = _infer_topology(table)

    user_ref = _pick(rec, ["customer_reference", "user_id", "subject_reference", "subject_id", "customer"])
    name = _pick(rec, ["full_name", "display_name", "requester_name", "name"])
    email = _pick(rec, ["email", "owner_email", "customer_email", "requester_email", "lookup_key", "subject_email"])
    phone = _pick(rec, ["phone", "owner_phone", "requester_phone", "customer_phone"])
    email_hash = _pick(rec, ["email_hash", "hash"])

    status = _pick(rec, ["account_status", "status", "state", "enabled", "retention_status"])
    retention_until = _pick(rec, ["retention_until", "retained_until"])
    hold = _pick(rec, ["legal_hold", "hold", "hold_reason"])
    enabled = _pick(rec, ["enabled", "data_flow_enabled"])
    restore_allowed = _pick(rec, ["restore_allowed", "restore_enabled"])

    present = True
    if status is not None and isinstance(status, str) and status.upper() in ("DELETED", "ABSENT", "REMOVED", "NOT_FOUND"):
        present = False
    extra = {"table": table}
    source_resource = _pick(rec, ["source_resource_id", "source_resource"])
    if source_resource:
        extra["source_resource_id"] = str(source_resource)
    if status is not None:
        extra["status"] = str(status)
    if retention_until is not None:
        extra["retention_until"] = str(retention_until)
    if hold is not None:
        extra["hold"] = str(hold)
    if enabled is not None:
        extra["enabled"] = bool(int(str(enabled))) if str(enabled).isdigit() else bool(str(enabled).lower() in ("1", "true", "yes"))
    if restore_allowed is not None:
        # 1/0 flag (or true/false) that decides REPORT_RESTORE_RISK vs a clean
        # VERIFY_RESTORE_BLOCKED for a BACKUP_ARCHIVE row.
        extra["restore_allowed"] = (bool(int(str(restore_allowed)))
                                    if str(restore_allowed).isdigit()
                                    else str(restore_allowed).lower() in ("1", "true", "yes"))
    extra["record_exists"] = present
    if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
        extra["anonymized"] = not bool(email or phone or email_hash or user_ref)
    if category == "ACCOUNT_TOMBSTONE":
        extra["redacted"] = not bool(name or email or phone or email_hash)

    idents = {}
    for k, v in (
        ("user_ref", user_ref),
        ("name", name),
        ("email", email),
        ("phone", phone),
        ("email_hash", email_hash),
    ):
        if v is not None and str(v).strip() != "":
            idents[k] = v

    return Fact(
        file_id=file_id,
        captured_at=captured_at,
        system_id=system_id,
        resource_id=str(resource_id or ""),
        category=category,
        presence="PRESENT" if present else "ABSENT",
        identifiers=idents,
        extra=extra,
        topology=topology,
    )


_SYSTEM_BY_TABLE = [
    (r"customer_profiles", "account_service", "USER_PROFILE"),
    (r"customer_search_documents", "search_service", "SEARCH_INDEX_ENTRY"),
    (r"search_documents", "search_service", "SEARCH_INDEX_ENTRY"),
    (r"customer_analytics", "analytics_platform", "ANALYTICS_PROFILE"),
    (r"support_records", "support_platform", "SUPPORT_MESSAGE"),
    (r"customer_exports", "export_service", "USER_EXPORT"),
    (r"connector_subscriptions", "integration_service", "DATA_FLOW"),
    (r"backup_restore", "backup_service", "BACKUP_ARCHIVE"),
    (r"security_audit", "security_service", "SECURITY_AUDIT_LOG"),
    (r"legal_hold", "legal_service", "LEGAL_HOLD_RECORD"),
    (r"tombstone", "identity_service", "ACCOUNT_TOMBSTONE"),
    # Generic fallbacks: table names are free-form across snapshot vintages
    # (e.g. search_documents_primary), so match on the domain token.
    (r"search", "search_service", "SEARCH_INDEX_ENTRY"),
    (r"analytics|telemetry", "analytics_platform", "ANALYTICS_PROFILE"),
    (r"support", "support_platform", "SUPPORT_MESSAGE"),
    (r"export", "export_service", "USER_EXPORT"),
    (r"connector|integration", "integration_service", "DATA_FLOW"),
    (r"backup|restore", "backup_service", "BACKUP_ARCHIVE"),
    (r"security", "security_service", "SECURITY_AUDIT_LOG"),
    (r"legal", "legal_service", "LEGAL_HOLD_RECORD"),
    (r"profile", "account_service", "USER_PROFILE"),
    (r"account", "account_service", "USER_PROFILE"),
]

_TOPO_RE = [
    (r"failover", "failover_replica"),
    (r"read_replica", "read_replica"),
    (r"reporting_replica", "reporting_replica"),
    (r"regional", "regional_replica"),
    (r"primary", "primary"),
]


def _infer_system(table):
    t = (table or "").lower()
    for pat, system_id, _cat in _SYSTEM_BY_TABLE:
        if re.search(pat, t, re.I):
            return system_id
    return "unknown"


def _infer_category(table):
    t = (table or "").lower()
    for pat, system_id, category in _SYSTEM_BY_TABLE:
        if re.search(pat, t, re.I):
            return category
    return "UNKNOWN"


def _infer_topology(table):
    t = (table or "").lower()
    for pat, topo in _TOPO_RE:
        if re.search(pat, t, re.I):
            return topo
    return "primary"
