"""Typed dataclasses and CSV loaders for all participant-facing dataset files."""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from audit.config import (
    ACTIVITY, AUDIT_LOG, DELETION_REQUESTS, IDENTITIES, RULES,
)


@dataclass
class DeletionRequest:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str


@dataclass
class AuditLogEntry:
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass
class ActivityEvent:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass
class LoaderResult:
    requests: list = field(default_factory=list)
    identities: list = field(default_factory=list)
    audit_log: list = field(default_factory=list)
    activity: list = field(default_factory=list)
    rules: list = field(default_factory=list)


def _rows(path) -> list[dict]:
    path = Path(path)
    out = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("case_id") or row.get("audit_id") or row.get("activity_id") or row.get("rule_id") or row.get("user_id") or row.get("file_id")):
                continue  # skip blank filler rows
            out.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return out


def load_deletion_requests(path=DELETION_REQUESTS) -> list[DeletionRequest]:
    out = []
    for r in _rows(path):
        out.append(DeletionRequest(
            case_id=r.get("case_id", ""),
            email=r.get("email", ""),
            requested_at=r.get("requested_at", ""),
            reported_status=r.get("reported_status", ""),
            reported_at=r.get("reported_at", ""),
            audit_at=r.get("audit_at", ""),
            request_text=r.get("request_text", ""),
        ))
    return out


def load_identities(path=IDENTITIES) -> list[UserIdentity]:
    out = []
    for r in _rows(path):
        out.append(UserIdentity(
            user_id=r.get("user_id", ""),
            name=r.get("name", ""),
            email=r.get("email", ""),
            phone=r.get("phone", ""),
            email_hash=r.get("email_hash", ""),
            updated_at=r.get("updated_at", ""),
        ))
    return out


def load_audit_log(path=AUDIT_LOG) -> list[AuditLogEntry]:
    out = []
    for r in _rows(path):
        out.append(AuditLogEntry(
            audit_id=r.get("audit_id", ""),
            user_id=r.get("user_id", ""),
            event_time=r.get("event_time", ""),
            field_name=r.get("field_name", ""),
            old_value=r.get("old_value", ""),
            new_value=r.get("new_value", ""),
        ))
    return out


def load_activity(path=ACTIVITY) -> list[ActivityEvent]:
    out = []
    for r in _rows(path):
        out.append(ActivityEvent(
            activity_id=r.get("activity_id", ""),
            event_time=r.get("event_time", ""),
            event_type=r.get("event_type", ""),
            source_system=r.get("source_system", ""),
            target_system=r.get("target_system", ""),
            resource_id=r.get("resource_id", ""),
            reported_result=r.get("reported_result", ""),
            details=r.get("details", ""),
        ))
    return out


def load_rules(path=RULES) -> list[DeletionRule]:
    out = []
    for r in _rows(path):
        out.append(DeletionRule(
            rule_id=r.get("rule_id", ""),
            data_category=r.get("data_category", ""),
            condition=r.get("condition", ""),
            required_action=r.get("required_action", ""),
        ))
    return out


def load_all() -> LoaderResult:
    return LoaderResult(
        requests=load_deletion_requests(),
        identities=load_identities(),
        audit_log=load_audit_log(),
        activity=load_activity(),
        rules=load_rules(),
    )
