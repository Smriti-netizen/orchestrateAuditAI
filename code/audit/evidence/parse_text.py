"""Plain-text evidence parser: shift-handover and similar human-readable logs."""
import re

from .facts import ABSENT, PRESENT, Fact

_LINE = re.compile(
    r"^\s*[-*]?\s*(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[Z]?)\s*\|\s*"
    r"(?P<system>[\w.]+)\s*\|\s*(?P<resource>[\w.]+)\s*\|\s*"
    r"(?P<category>[\w_]+)\s*\|\s*(?P<state>[\w_]+)\s*\|\s*(?P<ident>.+?)\s*$"
)


def parse_text_file(path, file_id, captured_at):
    facts, errors = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = _LINE.match(raw)
            if not m:
                continue
            state = (m.group("state") or "").upper()
            presence = ABSENT if state in ("NOT_FOUND", "ABSENT", "MISSING", "DELETED") else PRESENT
            ident = (m.group("ident") or "").strip()
            email = ident if "@" in ident and not ident.startswith("user_") else None
            user_ref = None if (email or "@" in ident) else ident
            category = (m.group("category") or "UNKNOWN").upper()
            extra = {"record_exists": presence == PRESENT}
            # Handover rows encode the actionable state directly in the state column.
            if category == "BACKUP_ARCHIVE":
                extra["restore_allowed"] = state == "ACTIVE"
            elif category == "DATA_FLOW":
                extra["enabled"] = state == "ACTIVE"
            if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
                extra["anonymized"] = state == "ANONYMIZED"
            if category == "SUPPORT_MESSAGE":
                extra["redacted"] = state == "REDACTED"
            facts.append(Fact(
                file_id=file_id,
                captured_at=m.group("ts") or captured_at,
                system_id=m.group("system") or "unknown",
                resource_id=m.group("resource") or "",
                category=category,
                presence=presence,
                identifiers={"email": email, "user_ref": user_ref} if (email or user_ref) else {},
                extra=extra,
                topology="primary",
            ))
    return facts, errors
