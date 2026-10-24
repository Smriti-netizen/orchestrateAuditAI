"""Normalised Fact model and the in-memory fact index (M1 milestone)."""
from dataclasses import dataclass, field

PRESENT = "PRESENT"
ABSENT = "ABSENT"

VALID_PRESENCE = (PRESENT, ABSENT)


@dataclass
class Fact:
    """One normalised evidence fact. Parsers translate source formats -> this."""

    file_id: str          # citation id, e.g. evidence_54
    captured_at: str      # ISO time of this snapshot/row (inclusive cutoff source)
    system_id: str        # account_service, search_service, ...
    resource_id: str      # account_005, search_001, ...
    category: str         # USER_PROFILE, SEARCH_INDEX_ENTRY, ...
    presence: str         # PRESENT | ABSENT
    identifiers: dict = field(default_factory=dict)   # user_ref/name/email/phone/email_hash...
    extra: dict = field(default_factory=dict)        # topology, enabled, status, retention...
    topology: str = "n/a"   # primary | regional_replica | reporting_replica | failover_replica | n/a
    covers: bool = False    # True only when this record's source fully enumerates its collection


SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "backup_service": "BACKUP_ARCHIVE",
    "integration_service": "DATA_FLOW",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "security_service": "SECURITY_AUDIT_LOG",
    "marketing_platform": "MARKETING_PROFILE",
    "cache_service": "CACHE_ENTRY",
}


def category_for(system_id, collection=None, resource_id=None, extra=None):
    base = SYSTEM_CATEGORY.get(system_id)
    if base:
        return base
    # Last resort: some business systems are not in ``SYSTEM_CATEGORY`` at all,
    # so their facts would stay UNKNOWN and the record would silently drop out
    # of the plan.  Only those prefixes are mapped here; every prefix that a
    # parser already classifies is deliberately absent, because a cross-system
    # snapshot row (a ``support_*`` id inside a replica dump, say) must keep the
    # category the row itself proves, not the one its id happens to suggest.
    if resource_id:
        for prefix, category in PREFIX_CATEGORY.items():
            if resource_id == prefix or resource_id.startswith(prefix + "_"):
                return category
        if collection:
            return category_from_collection(collection)
    return "UNKNOWN"


# Systems whose category cannot be read from ``SYSTEM_CATEGORY``: the legal
# service stores holds, which policy retains rather than deletes.
PREFIX_CATEGORY = {
    "legal_hold": "LEGAL_HOLD_RECORD",
}


def category_from_collection(collection):
    c = (collection or "").lower()
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
    return "UNKNOWN"


class FactIndex:
    """Simple in-memory index over normalised facts."""

    def __init__(self):
        self.facts = []

    def add(self, fact: Fact):
        self.facts.append(fact)

    def __len__(self):
        return len(self.facts)

    def by_resource(self, system_id: str, resource_id: str):
        return [f for f in self.facts
                if f.system_id == system_id and f.resource_id == resource_id]

    def by_file(self, file_id: str):
        return [f for f in self.facts if f.file_id == file_id]

    def by_system(self, system_id: str):
        return [f for f in self.facts if f.system_id == system_id]
