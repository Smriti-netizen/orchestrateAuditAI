"""Identifier-only resource linking + snapshot coverage (no timestamp guessing).

A resource belongs to a user only when a fact or explicit link carries one of the
user's exact identifiers together with a resource id:
  * snapshot rows / log lines / JSON rows / image rows / PDF rows,
  * LINK_RESOURCE_IDENTITY activity rows ("linked ... to <email> and <user_id>"),
  * source_resource_id columns (search document -> source account).

Absence is only ever proved by coverage: a snapshot that enumerates the whole
collection, captured at or before audit_at, that does not contain the resource.
"""
import re
from dataclasses import dataclass, field


def digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def idents_match(identifiers, ids):
    """Exact-only match of a fact's identifiers to a user's identifier set."""
    identifiers = identifiers or {}
    ref = str(identifiers.get("user_ref") or "").strip()
    if ref and ref == ids["user_id"]:
        return True
    email = str(identifiers.get("email") or "").strip().lower()
    if email and email in ids["emails"]:
        return True
    h = str(identifiers.get("email_hash") or "").strip().lower()
    if h and h in ids["hashes"]:
        return True
    phone = digits(identifiers.get("phone"))
    if phone and phone in ids["phones"]:
        return True
    return False


def system_for_resource(resource_id):
    rid = (resource_id or "").lower()
    for prefix, sid in (("account_", "account_service"), ("search_", "search_service"),
                        ("analytics_", "analytics_platform"), ("support_", "support_platform"),
                        ("export_", "export_service"), ("backup_", "backup_service"),
                        ("data_flow_", "integration_service"),
                        ("audit_record_", "security_service"),
                        ("tombstone_", "identity_service"),
                        ("marketing_", "marketing_platform")):
        if rid.startswith(prefix):
            return sid
    if rid.startswith(("ord-", "fin-")):
        return "finance_service"
    return "unknown"


def normalize_system(system_id):
    """Map evidence-side store names onto the canonical system ids in the answer."""
    s = (system_id or "").strip().lower()
    aliases = {
        "user_export_store": "export_service",
        "analytics_identity_store": "analytics_platform",
        "search_index_service": "search_service",
        "search_index_admin": "search_service",
        "customer_account_service": "account_service",
        "support_replica": "support_platform",
        "legacy_contact_index": "support_platform",
        "records_assurance_service": "account_service",
    }
    if s in aliases:
        return aliases[s]
    for token, sid in (
        ("export", "export_service"), ("analytic", "analytics_platform"),
        ("telemetry", "analytics_platform"), ("search", "search_service"),
        ("support", "support_platform"), ("backup", "backup_service"),
        ("integration", "integration_service"), ("connector", "integration_service"),
        ("identity", "identity_service"), ("security", "security_service"),
        ("audit", "security_service"), ("account", "account_service"),
    ):
        if token in s:
            return sid
    return system_id


@dataclass
class LinkedResource:
    system_id: str
    resource_id: str
    category: str = "UNKNOWN"
    facts: list = field(default_factory=list)     # every Fact touching this resource
    link_sources: set = field(default_factory=set)  # "fact" | "activity" | "source_resource_id"

    @property
    def evidence_ids(self):
        return sorted({f.file_id for f in self.facts})


@dataclass
class Coverage:
    file_id: str
    captured_at: str
    system_id: str
    collection: str
    topology: str
    resource_ids: frozenset
    file_captured_at: str = ""


def build_coverage(facts):
    """One coverage record per (file, system, collection, topology) snapshot."""
    groups = {}
    for f in facts:
        if not f.covers:
            continue
        key = (f.file_id, f.system_id, str(f.extra.get("collection") or ""), f.topology)
        entry = groups.get(key)
        if entry is None:
            entry = {"captured_at": f.captured_at, "ids": set(),
                     "file_captured_at": f.extra.get("file_captured_at") or f.captured_at}
            groups[key] = entry
        entry["ids"].add(f.resource_id)
        if f.captured_at > entry["captured_at"]:
            entry["captured_at"] = f.captured_at
    return [Coverage(file_id=k[0], captured_at=v["captured_at"], system_id=k[1],
                     collection=k[2], topology=k[3], resource_ids=frozenset(v["ids"]),
                     file_captured_at=v["file_captured_at"])
            for k, v in sorted(groups.items())]


def absence_evidence(coverage, system_id, resource_id, audit_at,
                     resource_present_anywhere=False):
    """Best snapshot at/before audit_at that covers the system and lacks the resource.

    Returns (file_id, candidates) where candidates are all qualifying snapshots,
    newest first (calibration uses this to test the 'latest wins' hypothesis).
    """
    if resource_present_anywhere:
        return None, []
    by_file = {}
    for cov in coverage:
        if cov.system_id != system_id:
            continue
        # The manifest capture time governs admissibility, not the embedded
        # snapshot time: a bundle exported after audit_at cannot be cited.
        if (cov.file_captured_at or cov.captured_at) > audit_at:
            continue
        if cov.captured_at > audit_at:
            continue
        slot = by_file.setdefault(cov.file_id, {"captured_at": cov.captured_at,
                                                "ids": set()})
        slot["ids"] |= cov.resource_ids
        slot["captured_at"] = max(slot["captured_at"], cov.captured_at)
    candidates = [(fid, v["captured_at"]) for fid, v in by_file.items()
                  if resource_id not in v["ids"]]
    # Newest snapshot first; file id breaks ties deterministically.
    candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return (candidates[0][0] if candidates else None), candidates


def present_evidence(facts, system_id, resource_id, audit_at):
    """Latest PRESENT fact at/before audit_at (the state we must remediate or prove)."""
    hits = [f for f in facts
            if f.system_id == system_id and f.resource_id == resource_id
            and f.captured_at <= audit_at]
    if not hits:
        return None
    hits.sort(key=lambda f: (f.captured_at, f.file_id))
    return hits[-1]


class Linker:
    """Resolve user -> resources using identifiers and explicit links only."""

    LINK_RE = re.compile(r"linked\s+(\S+)\s+\(([^)]*)\)\s+to\s+(\S+@\S+?)\s+and\s+(user_\d+)",
                         re.I)

    def __init__(self, resolver, facts, activity=None):
        self.resolver = resolver
        self.facts = facts
        self.activity = activity or []
        self._links = {}
        self._index_activity()
        self._cache = {}
        # (system, resource) -> every fact about that resource id. Once a
        # resource is linked to the subject by one identifying row, its other
        # rows (e.g. a 404 profile lookup with no identifiers of its own) are
        # state evidence for that same resource.
        self._by_resource = {}
        for f in facts:
            self._by_resource.setdefault((f.system_id, f.resource_id), []).append(f)

    def _index_activity(self):
        for row in self.activity:
            if (row.event_type or "").upper() != "LINK_RESOURCE_IDENTITY":
                continue
            m = self.LINK_RE.search(row.details or "")
            if not m:
                continue
            resource_id, _paren, _email, user_id = m.groups()
            system_id = row.target_system or system_for_resource(resource_id)
            self._links.setdefault(user_id, {})[(system_id, resource_id)] = row.activity_id

    def facts_for(self, system_id, resource_id):
        """Facts indexed for one (system, resource), regardless of linking."""
        return list(self._by_resource.get((system_id, resource_id), []))

    def resources(self, user_id):
        """dict[(system, resource)] -> LinkedResource for one user."""
        if user_id in self._cache:
            return self._cache[user_id]
        ids = self.resolver.user_identifier_set(user_id)
        out = {}

        def add(system_id, resource_id, fact=None, source="fact"):
            if not resource_id:
                return
            if not system_id or system_id == "unknown":
                system_id = system_for_resource(resource_id)
            key = (system_id, resource_id)
            item = out.setdefault(key, LinkedResource(system_id=key[0],
                                                      resource_id=resource_id))
            if fact is not None:
                item.facts.append(fact)
                if fact.category and fact.category != "UNKNOWN":
                    item.category = fact.category
            item.link_sources.add(source)

        for fact in self.facts:
            if not idents_match(fact.identifiers, ids):
                continue
            add(fact.system_id, fact.resource_id, fact)
            source = (fact.extra or {}).get("source_resource_id")
            if source:
                add(system_for_resource(source), source, fact, "source_resource_id")

        for (system_id, resource_id), activity_id in self._links.get(user_id, {}).items():
            add(system_id, resource_id, None, "activity")

        # Fold in the remaining rows that share a linked resource id but carry
        # no identifying field themselves (absence lookups, counts, flags).
        for key, item in out.items():
            known = {id(f) for f in item.facts}
            for fact in self._by_resource.get(key, []):
                if id(fact) not in known:
                    item.facts.append(fact)
                    known.add(id(fact))
                    item.link_sources.add("resource")
            item.facts.sort(key=lambda f: (f.captured_at, f.file_id))

        self._cache[user_id] = out
        return out

    def unresolved_systems(self, user_id, expected=()):
        """Systems with no linked resource at all (reported instead of guessed)."""
        linked = {k[0] for k in self.resources(user_id)}
        return [s for s in expected if s not in linked]
