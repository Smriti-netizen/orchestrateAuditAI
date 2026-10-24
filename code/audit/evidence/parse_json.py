"""JSON evidence parser: snapshot bundles, service_exports maps, flat records manifests."""
import json

from .facts import ABSENT, PRESENT, Fact


def parse_json_file(path, file_id, captured_at):
    facts = []
    errors = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        try:
            data = json.load(fh)
        except Exception as exc:
            return [], [f"json decode error: {exc}"]

    if not isinstance(data, dict):
        return [], ["top level is not an object"]

    if "service_exports" in data and isinstance(data["service_exports"], dict):
        for service, group in data["service_exports"].items():
            if not isinstance(group, dict):
                continue
            for gk, arr in group.items():
                if not isinstance(arr, list):
                    continue
                for rec in arr:
                    f = _from_service_export(service, gk, rec, file_id, captured_at)
                    if f:
                        facts.append(f)
    elif "snapshots" in data and isinstance(data["snapshots"], list):
        for snap in data["snapshots"]:
            if not isinstance(snap, dict):
                continue
            snap_captured = str(snap.get("captured_at") or captured_at)
            data_store = str(snap.get("data_store") or "unknown")
            collection = str(snap.get("collection") or "")
            for rec in snap.get("records") or []:
                    f = _from_record(
                        data_store=data_store, collection=collection, rec=rec,
                        file_id=file_id, captured_at=snap_captured)
                    if f:
                        f.covers = True  # bundle snapshot enumerates the collection
                        f.extra["collection"] = collection
                        f.topology = _topology_for(data_store)
                        facts.append(f)
    elif "records" in data and isinstance(data["records"], list):
        top_captured = str(data.get("captured_at") or data.get("exported_at") or captured_at)
        collection = str(data.get("collection") or "")
        origin = str(data.get("origin") or data.get("source") or "")
        # A flat manifest with a collection is a full dump; lookup-style files
        # ("result": NOT_FOUND / per-subject rows) are not.
        is_manifest = bool(collection) and not any(
            isinstance(r, dict) and "result" in r for r in data["records"])
        for rec in data["records"]:
            f = _from_record(
                data_store=origin or "unknown", collection=collection, rec=rec,
                file_id=file_id, captured_at=top_captured)
            if f:
                f.covers = is_manifest
                f.extra["collection"] = collection
                facts.append(f)

    return facts, errors


def _from_service_export(service, gk, rec, file_id, captured_at):
    resource_id = str(rec.get("resource_id") or "")
    system_id = service or "unknown"
    category = _category_for_service(service, gk, rec)
    extra = {}
    presence = PRESENT
    identifiers = {}
    user_ref = rec.get("customer_reference") or rec.get("subject_selector") or rec.get("subject_reference")
    email = rec.get("customer_email") or rec.get("owner_email") or rec.get("email") or rec.get("lookup_key") or rec.get("subject_email") or rec.get("requester_email")
    phone = rec.get("owner_phone") or rec.get("requester_phone") or rec.get("customer_phone")
    email_hash = rec.get("email_hash")
    name = rec.get("display_name") or rec.get("full_name") or rec.get("requester_name")

    if gk == "profile_lookups":
        status_code = rec.get("status_code")
        profile = rec.get("profile") or {}
        presence = PRESENT if (status_code is None or int(status_code) == 200) else ABSENT
        user_ref = profile.get("customer_reference") or user_ref
        email = profile.get("email") or email
        phone = profile.get("phone") or phone
        email_hash = profile.get("email_hash") or email_hash
        name = profile.get("full_name") or name
        extra = {"status_code": status_code, "lookup_key": rec.get("lookup_key")}
    elif gk == "profiles":
        presence = PRESENT if bool(rec.get("email") or rec.get("user_id") or rec.get("customer_reference") or rec.get("email_hash")) else ABSENT
        user_ref = rec.get("customer_reference") or rec.get("user_id")
        email = rec.get("email")
        phone = rec.get("phone")
        email_hash = rec.get("email_hash")
        name = rec.get("name")
        extra = {"audience_cohort": rec.get("audience_cohort")}
    elif gk == "restore_entries":
        presence = PRESENT
        extra = {"restore_allowed": bool(rec.get("restore_allowed"))}
    elif gk == "exports":
        presence = PRESENT
        extra = {"download_available": bool(rec.get("download_available"))}
    elif gk == "connectors":
        presence = PRESENT
        enabled = bool(rec.get("enabled"))
        extra = {"enabled": enabled, "connector_name": rec.get("connector_name"), "mode": rec.get("mode")}
        extra["record_exists"] = True
        return Fact(
            file_id=file_id, captured_at=str(rec.get("observed_at") or captured_at),
            system_id=system_id, resource_id=str(rec.get("resource_id") or ""),
            category=category, presence=PRESENT, identifiers=_idents(user_ref, name, email, phone, email_hash),
            extra=extra, topology="primary")
    elif gk == "index_queries":
        docs = (rec.get("hits") or {}).get("documents") or []
        presence = PRESENT if docs else ABSENT
        first = docs[0] if docs else {}
        user_ref = first.get("customer_reference") or user_ref
        email = first.get("email") or email
        phone = first.get("phone") or phone
        email_hash = first.get("email_hash") or email_hash
        name = first.get("display_name") or first.get("full_name") or name
        extra = {"query": rec.get("query"), "total": (rec.get("hits") or {}).get("total")}
    elif gk == "audit_records":
        presence = PRESENT
        extra = {"event_code": rec.get("event_code") or rec.get("event_name"), "retention_until": rec.get("retention_until")}
    elif gk == "tickets":
        presence = PRESENT
        extra = {"message_excerpt": rec.get("message_excerpt")}

    # Explicit existence/redaction flags so the state engine never confuses
    # "row present but already anonymised" with "row is gone".  A miss is not
    # existence: only a present observation, or the retained anonymised /
    # redacted form, means the record still exists at the observation time.
    if gk in ("profiles",):
        extra["anonymized"] = not bool(email or phone or email_hash or user_ref)
    if gk in ("tickets",):
        extra["redacted"] = not bool(email or phone or name)
    extra["record_exists"] = _record_exists(presence, extra)

    return Fact(
        file_id=file_id, captured_at=str(rec.get("observed_at") or captured_at),
        system_id=system_id, resource_id=str(rec.get("resource_id") or ""),
        category=category, presence=presence,
        identifiers=_idents(user_ref, name, email, phone, email_hash),
        extra=extra, topology="primary")


def _category_from_collection(collection):
    c = (collection or "").lower()
    if "deleted_customer_reference" in c or "tombstone" in c:
        return "ACCOUNT_TOMBSTONE"
    if "security_audit" in c or "audit_event" in c:
        return "SECURITY_AUDIT_LOG"
    if "search" in c:
        return "SEARCH_INDEX_ENTRY"
    if "analytics" in c or "telemetry" in c:
        return "ANALYTICS_PROFILE"
    if "backup" in c:
        return "BACKUP_ARCHIVE"
    if "export" in c:
        return "USER_EXPORT"
    if "connector" in c or "integration" in c or "flow" in c:
        return "DATA_FLOW"
    if "security" in c or "audit" in c:
        return "SECURITY_AUDIT_LOG"
    if "legal" in c:
        return "LEGAL_HOLD_RECORD"
    if "support" in c:
        return "SUPPORT_MESSAGE"
    if "cache" in c:
        return "CACHE_ENTRY"
    if "marketing" in c or "campaign" in c:
        return "MARKETING_PROFILE"
    if "profile" in c or "account" in c:
        return "USER_PROFILE"
    return "UNKNOWN"


def _record_exists(presence, extra):
    """Whether the observation proves the row still exists.

    A hit is existence; a miss is not.  A row that is observed only in its
    retained anonymised/redacted form also exists - that form is the outcome
    the policy asks for, not absence.
    """
    return bool(presence == PRESENT or extra.get("anonymized") or extra.get("redacted"))


def _from_record(data_store, collection, rec, file_id, captured_at):
    resource_id = str(rec.get("resource_id") or "")
    system_id = data_store or "unknown"
    category = _category_from_collection(collection)
    # Search documents nest the identifying fields one level down.
    doc = rec.get("document") if isinstance(rec.get("document"), dict) else {}
    merged = dict(doc)
    merged.update({k: v for k, v in rec.items() if v is not None})
    rec = merged
    user_ref = rec.get("customer_reference") or rec.get("user_id") or rec.get("subject_reference") or rec.get("subject_selector")
    name = rec.get("full_name") or rec.get("display_name") or rec.get("requester_name") or rec.get("name")
    email = rec.get("email") or rec.get("owner_email") or rec.get("requester_email") or rec.get("customer_email") or rec.get("lookup_key") or rec.get("subject_email")
    phone = rec.get("phone") or rec.get("owner_phone") or rec.get("requester_phone") or rec.get("customer_phone")
    email_hash = rec.get("email_hash")
    presence = PRESENT
    extra = {}
    if "profile" in rec and rec["profile"] is None:
        presence = ABSENT
    if "status_code" in rec:
        presence = PRESENT if int(rec["status_code"]) == 200 else ABSENT
    if "result" in rec:
        presence = PRESENT if str(rec.get("result")).lower() == "hit" else ABSENT
    if "rows" in rec:
        presence = PRESENT if int(rec.get("rows") or 0) > 0 else ABSENT
    if "available" in rec:
        presence = PRESENT if bool(rec.get("available")) else ABSENT
    if "download_available" in rec:
        presence = PRESENT if bool(rec.get("download_available")) else ABSENT
    if "retention_reason" in rec:
        extra["retention_reason"] = rec.get("retention_reason")
    if "retained_until" in rec:
        extra["retention_until"] = rec.get("retained_until")
    if rec.get("source_resource_id"):
        extra["source_resource_id"] = rec["source_resource_id"]
    if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
        extra["anonymized"] = not bool(email or phone or email_hash or user_ref)
    if category == "ACCOUNT_TOMBSTONE":
        extra["redacted"] = not bool(name or email or phone or email_hash)
    extra["record_exists"] = _record_exists(presence, extra)
    return Fact(
        file_id=file_id, captured_at=str(captured_at or rec.get("observed_at") or rec.get("captured_at") or ""),
        system_id=system_id, resource_id=resource_id, category=category, presence=presence,
        identifiers=_idents(user_ref, name, email, phone, email_hash), extra=extra, topology="primary")


def _idents(user_ref, name, email, phone, email_hash):
    out = {}
    for k, v in {
        "user_ref": user_ref, "name": name, "email": email, "phone": phone, "email_hash": email_hash,
    }.items():
        if v is not None and str(v).strip() != "":
            out[k] = v
    return out


def _topology_for(data_store):
    store = (data_store or "").lower()
    for token in ("failover", "read_replica", "reporting", "regional"):
        if token in store:
            return "failover_replica" if token == "failover" else (
                "read_replica" if token == "read_replica" else (
                    "reporting_replica" if token == "reporting" else "regional_replica"))
    return "primary"


def _category_for_service(service, gk, rec):
    base = {
        "account_service": "USER_PROFILE",
        "search_service": "SEARCH_INDEX_ENTRY",
        "analytics_platform": "ANALYTICS_PROFILE",
        "backup_service": "BACKUP_ARCHIVE",
        "export_service": "USER_EXPORT",
        "integration_service": "DATA_FLOW",
        "security_service": "SECURITY_AUDIT_LOG",
        "support_platform": "SUPPORT_MESSAGE",
    }.get(service, "UNKNOWN")
    if gk == "connectors":
        return "DATA_FLOW"
    if gk == "tickets":
        return "SUPPORT_MESSAGE"
    if gk == "audit_records":
        return "SECURITY_AUDIT_LOG"
    if gk == "restore_entries":
        return "BACKUP_ARCHIVE"
    if gk == "exports":
        return "USER_EXPORT"
    if gk == "index_queries":
        return "SEARCH_INDEX_ENTRY"
    if gk == "profiles":
        return "ANALYTICS_PROFILE"
    if gk == "profile_lookups":
        return "USER_PROFILE"
    if base != "UNKNOWN":
        return base
    # Service the static map cannot name at all (e.g. legal_service): fall back
    # to the resource-id prefix.
    from .facts import SYSTEM_CATEGORY, category_for
    if service in SYSTEM_CATEGORY:
        return base
    return category_for(service, gk, rec.get("resource_id"))
