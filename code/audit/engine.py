"""State reconstruction, deletion-policy application, and step planning (M3).

Everything here is deterministic and evidence-driven:

* only facts from files captured at/before ``audit_at`` (manifest time) count;
* a record present in any replica counts as present (drift is not deletion);
* ``reported_status`` / ``reported_result`` are never consulted for state;
* findings cite evidence; remediation and post-remediation checks for an
  incomplete case are future work and therefore carry an empty evidence id.
"""
from dataclasses import dataclass, field

from audit.account_link import add_provenance_account, add_provenance_resource
from audit.evidence.facts import ABSENT, PRESENT, SYSTEM_CATEGORY

# category -> policy family
DELETE_CATEGORIES = {
    "USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY",
    "USER_EXPORT", "MARKETING_PROFILE", "SUPPORT_ATTACHMENT",
}
ANONYMIZE_CATEGORIES = {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}
REDACT_CATEGORIES = {"SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG", "ACCOUNT_TOMBSTONE"}
RETAIN_CATEGORIES = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}
# Finance/legal business records are retained and are not part of the step plan.
IGNORED_CATEGORIES = {"FINANCE_RECORD", "UNKNOWN"}

FINDING_ORDER = {
    "REPORT_REMAINING_RECORD": 0, "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2, "REPORT_RESTORE_RISK": 3,
}
REMEDIATION_ORDER = {
    "DISABLE_DATA_FLOW": 0, "BLOCK_RESTORE": 1, "DELETE_RECORD": 2,
    "REDACT_RECORD": 3, "ANONYMIZE_RECORD": 4,
}
VERIFICATION_ORDER = {
    "VERIFY_RECORD_ABSENT": 0, "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2, "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4, "VERIFY_RESTORE_BLOCKED": 5,
}


def _at_or_before(fact, audit_at):
    """Manifest capture time is authoritative; embedded row time must also pass.

    Log lines are the exception: the line itself is the observation, so its own
    timestamp decides admissibility (the file is only a later export container).
    """
    if not fact.captured_at or fact.captured_at > audit_at:
        return False
    if (fact.extra or {}).get("event_stream"):
        return True
    file_time = fact.extra.get("file_captured_at") or fact.captured_at
    return bool(file_time) and file_time <= audit_at


def _latest(facts):
    """Newest admissible fact, preferring an authoritative snapshot over a log line.

    A database row / bundle entry states the record's stored value; a log line is
    a transient observation of the same value. When both witness a violation the
    snapshot is the stronger citation, so it wins even if slightly older.
    """
    if not facts:
        return None
    return sorted(facts, key=lambda f: (not (f.extra or {}).get("event_stream"),
                                        f.captured_at, f.file_id))[-1]


def _pii_present(fact):
    """Direct identifiers that a REDACT/ANONYMIZE rule must remove."""
    ids = fact.identifiers or {}
    return any(str(ids.get(k) or "").strip() for k in ("name", "email", "phone", "email_hash"))


@dataclass
class Decision:
    system_id: str
    resource_id: str
    category: str
    finding: str = ""          # REPORT_* operation or ""
    remediation: str = ""      # DISABLE/BLOCK/DELETE/REDACT/ANONYMIZE or ""
    verification: str = ""
    verify_state: str = ""
    finding_evidence: str = ""
    verify_evidence: str = ""

    @property
    def is_violation(self):
        return bool(self.finding)


def _resolve_category(facts, fallback_system):
    for f in reversed(facts):
        if f.category and f.category != "UNKNOWN":
            return f.category
    return SYSTEM_CATEGORY.get(fallback_system, "UNKNOWN")


def _superseded(fact, resource_id, coverage, audit_at):
    """A later snapshot of the same collection that omits the row wins.

    Same system, collection and topology only: a fresh primary snapshot says
    nothing about a replica copy, so replica drift keeps counting.  The snapshot
    must be newer than the observation and admissible in its own right (both its
    embedded time and its manifest capture time at or before ``audit_at``).
    """
    collection = str((fact.extra or {}).get("collection") or "")
    for snap in coverage:
        if snap.system_id != fact.system_id or snap.topology != fact.topology:
            continue
        if snap.collection != collection:
            continue
        if not (fact.captured_at < snap.captured_at <= audit_at):
            continue
        if (snap.file_captured_at or snap.captured_at) > audit_at:
            continue
        if resource_id not in snap.resource_ids:
            return True
    return False


def decide_resource(system_id, resource_id, facts, coverage=None, audit_at=None):
    """Return a Decision for one linked resource, or None when not actionable."""
    if not facts:
        return None
    category = _resolve_category(facts, system_id)
    if category in IGNORED_CATEGORIES:
        return None

    # Presence at audit time: any admissible fact showing the record exists,
    # including a stale replica copy (replica drift keeps data alive) but not a
    # row that a later snapshot of the same collection shows gone.
    present = [f for f in facts
               if f.presence == PRESENT or f.extra.get("record_exists")]
    if coverage is not None and audit_at:
        present = [f for f in present
                   if not _superseded(f, resource_id, coverage, audit_at)]
    exists = bool(present)
    latest_present = _latest(present)

    def absent_evidence():
        absent = _latest([f for f in facts
                          if f.presence == "ABSENT" and not f.extra.get("record_exists")])
        return absent.file_id if absent else ""

    if category in DELETE_CATEGORIES:
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_RECORD_ABSENT", verify_state="ABSENT")
        if exists and latest_present:
            d.finding = "REPORT_REMAINING_RECORD"
            d.remediation = "DELETE_RECORD"
            d.finding_evidence = latest_present.file_id
        d.verify_evidence = absent_evidence()
        return d

    if category in ANONYMIZE_CATEGORIES:
        identifiable = [f for f in facts if _pii_present(f)]
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_RECORD_ANONYMIZED", verify_state="ANONYMIZED")
        if exists and identifiable:
            d.finding = "REPORT_EXPOSED_PII"
            d.remediation = "ANONYMIZE_RECORD"
            d.finding_evidence = _latest(identifiable).file_id
            d.verify_evidence = ""
        elif not exists:
            d.verification, d.verify_state = "VERIFY_RECORD_ABSENT", "ABSENT"
            d.verify_evidence = absent_evidence()
        else:
            d.verify_evidence = _latest(facts).file_id
        return d

    if category in REDACT_CATEGORIES:
        identifiable = [f for f in facts if _pii_present(f)]
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_RECORD_REDACTED", verify_state="REDACTED")
        if exists and identifiable:
            d.finding = "REPORT_EXPOSED_PII"
            d.remediation = "REDACT_RECORD"
            d.finding_evidence = _latest(identifiable).file_id
        elif not exists:
            d.verification, d.verify_state = "VERIFY_RECORD_ABSENT", "ABSENT"
            d.verify_evidence = absent_evidence()
        else:
            d.verify_evidence = _latest(facts).file_id
        return d

    if category == "DATA_FLOW":
        enabled = any(bool((f.extra or {}).get("enabled")) for f in facts)
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_DATA_FLOW_DISABLED", verify_state="DISABLED")
        if enabled:
            d.finding = "REPORT_ACTIVE_DATA_FLOW"
            d.remediation = "DISABLE_DATA_FLOW"
            d.finding_evidence = _latest([f for f in facts
                                          if (f.extra or {}).get("enabled")]).file_id
        else:
            d.verify_evidence = _latest(facts).file_id
        return d

    if category == "BACKUP_ARCHIVE":
        allowed = any(bool((f.extra or {}).get("restore_allowed")) for f in facts)
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_RESTORE_BLOCKED", verify_state="BLOCKED")
        if allowed:
            d.finding = "REPORT_RESTORE_RISK"
            d.remediation = "BLOCK_RESTORE"
            d.finding_evidence = _latest([f for f in facts
                                          if (f.extra or {}).get("restore_allowed")]).file_id
        else:
            d.verify_evidence = _latest(facts).file_id
        return d

    if category in RETAIN_CATEGORIES:
        if not exists:
            return None
        d = Decision(system_id, resource_id, category,
                     verification="VERIFY_RECORD_PRESENT", verify_state="PRESENT")
        d.verify_evidence = _latest([f for f in facts if f.presence == PRESENT]).file_id
        return d

    return None


def build_steps(decisions, status):
    """Serialise decisions into the ordered step list for output.csv."""
    findings, remediations, verifications = [], [], []
    for d in decisions:
        if d.finding:
            findings.append(_step(d.system_id, d.resource_id, "FINDING",
                                  d.finding, _finding_state(d.finding), d.finding_evidence))
            remediations.append(_step(d.system_id, d.resource_id, "REMEDIATION",
                                      d.remediation, _remediation_state(d.remediation), ""))
            verifications.append(_step(d.system_id, d.resource_id, "VERIFICATION",
                                       d.verification, d.verify_state, ""))
        else:
            evidence = d.verify_evidence if status == "COMPLETE" else ""
            verifications.append(_step(d.system_id, d.resource_id, "VERIFICATION",
                                       d.verification, d.verify_state, evidence))

    findings.sort(key=lambda s: (FINDING_ORDER[s["operation"]], s["system_id"], s["resource_id"]))
    remediations.sort(key=lambda s: (REMEDIATION_ORDER[s["operation"]], s["system_id"], s["resource_id"]))
    verifications.sort(key=lambda s: (VERIFICATION_ORDER[s["operation"]], s["system_id"], s["resource_id"]))

    steps = findings + remediations + verifications
    for i, step in enumerate(steps, 1):
        step["step_no"] = i
    return steps


def _step(system_id, resource_id, phase, operation, state, evidence_id):
    return {
        "step_no": 0,
        "phase": phase,
        "system_id": system_id,
        "resource_id": resource_id,
        "operation": operation,
        "state": state,
        "evidence_id": evidence_id or "",
    }


def _remediation_state(op):
    return {
        "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
        "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED",
        "ANONYMIZE_RECORD": "ANONYMIZED",
    }[op]


def _finding_state(op):
    return "ACTIVE" if op in ("REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK") else "PRESENT"


KNOWN_PREFIXES = (
    "account_", "search_", "analytics_", "support_", "export_", "backup_",
    "data_flow_", "audit_record_", "tombstone_", "marketing_", "cache_",
    "legal_hold_", "ORD-", "FIN-",
)


def analyze_case(req, resolver, linker, coverage, absence_fn,
                 provenance_account=None, provenance_analytics=None):
    """Full audit for one request -> (deletion_status, steps).

    ``provenance_account`` is an account resource id recovered for this case by
    ``audit.account_link`` (the identifier-less worker row).  It is treated like
    an identifier-linked resource for state purposes: there are no facts about
    it, so the covering snapshot that omits it is the absence proof.
    """
    if not authorizes_deletion(req.request_text):
        return "NOT_APPLICABLE", []

    uid = resolver.resolve(req.email)
    linked = linker.resources(uid) if uid else {}
    if provenance_account:
        # A provenance guess may only prove absence: a 404 lookup that names the
        # account row is admissible as the absence proof, while any "record is
        # present" observation about a guessed account could invent a finding.
        absent_only = [f for f in linker.facts_for("account_service", provenance_account)
                       if f.presence == ABSENT and not f.extra.get("record_exists")]
        linked = add_provenance_account(linked, provenance_account, absent_only)
    for resource_id, facts in sorted((provenance_analytics or {}).items()):
        linked = add_provenance_resource(linked, "analytics_platform", resource_id,
                                         "ANALYTICS_PROFILE", facts=facts)
    decisions = []
    for (system_id, resource_id) in sorted(linked):
        if not resource_id.startswith(KNOWN_PREFIXES):
            # A display label (e.g. a ticket code from a screenshot) is not a
            # system resource id; never turn it into a step.
            continue
        item = linked[(system_id, resource_id)]
        # A resource whose only membership evidence was captured after the cut-off
        # is not in scope: the answer may not be built from the future.  Explicit
        # links (an activity row, or a provenance claim inside the request window)
        # establish membership themselves.
        if {"activity", "activity_provenance"} & item.link_sources:
            if item.link_times and not any(t <= req.audit_at for t in item.link_times):
                continue
        elif item.facts and not any(_at_or_before(f, req.audit_at) for f in item.facts):
            continue
        # Only facts that belong to this system's copy are state evidence for it.
        admissible = [f for f in item.facts
                      if f.system_id == system_id and _at_or_before(f, req.audit_at)]
        # An absence observation (lookup that returned nothing) made before the
        # deletion request says nothing about the post-request state, so it can
        # neither prove nor establish ownership of the resource.
        facts = [f for f in admissible
                 if not (f.presence == "ABSENT" and f.extra.get("event_stream")
                         and f.captured_at < req.requested_at)]
        decision = decide_resource(system_id, resource_id, facts,
                                   coverage=coverage, audit_at=req.audit_at)
        if decision is None and not facts:
            item = linked[(system_id, resource_id)]
            category = item.category if item.category != "UNKNOWN" else \
                SYSTEM_CATEGORY.get(system_id, "UNKNOWN")
            # Linked through source_resource_id / an explicit identity link but
            # never observed: the covering snapshot that omits it is the proof.
            # Only records that must be deleted need this proof (a tombstone or
            # retained record that is not there is simply not in scope).
            if admissible or category not in DELETE_CATEGORIES:
                continue
            evidence = absence_fn(system_id, resource_id, req.audit_at) or ""
            if not evidence:
                continue
            decision = Decision(system_id, resource_id, category,
                                verification="VERIFY_RECORD_ABSENT",
                                verify_state="ABSENT", verify_evidence=evidence)
        if decision is None:
            continue
        if decision.verification == "VERIFY_RECORD_ABSENT" and not decision.verify_evidence:
            decision.verify_evidence = absence_fn(
                system_id, resource_id, req.audit_at) or ""
        decisions.append(decision)

    status = "INCOMPLETE" if any(d.is_violation for d in decisions) else "COMPLETE"
    return status, build_steps(decisions, status)


# --------------------------------------------------------------------------
# Request authorization classifier
# --------------------------------------------------------------------------
NON_DELETION_MARKERS = (
    "not asking for deletion", "not a request to erase", "do not close",
    "don't delete", "do not delete", "not delete", "without removing",
    "leave the account", "leave my profile", "keep the account",
    "keep my account", "as it is", "still need the account",
    "still use it", "continue using the service", "marketing lists only",
    "information request", "resetting the login", "change the email address",
    "copy of the information", "download my account information",
    "please replace it", "correct it", "stop the promotional emails",
)

DELETION_MARKERS = (
    "delet", "eras", "remov", "deletion request", "permanent account-deletion",
    "close my account", "close the account", "suppression", "supprim",
    "löschen", "eliminar", "excluir", "exclusão", "削除", "삭제",
    # Follow-ups that ask the agent to confirm a prior deletion. These are
    # deletion cases even though the message never says "delete" itself.
    "profile is gone", "account is gone", "account no longer exists",
    "cannot identify me", "can't identify me", "can not identify me",
    "fully removed", "was removed", "been removed", "already been erased",
)


def classify_disagreements(req, uid, linked, activity):
    """Cross-check ``CLASSIFY_REQUEST`` rows against our own text classifier.

    Those rows are the privacy-intake team's *claim* ("classified ... as
    non-deletion"). They are never proof, so the text classifier still decides.
    We only report a disagreement when the row can be attributed to this
    subject - its resource id is one we linked, or its details name the user id
    or email - which makes an honest audit trail instead of a silent override.
    """
    attributed = {rid for (_system, rid) in linked}
    claim = authorizes_deletion(req.request_text)
    out = []
    for a in activity:
        if a.event_type != "CLASSIFY_REQUEST":
            continue
        details = (a.details or "").lower()
        if a.resource_id not in attributed and not (
                (uid and uid.lower() in details) or
                (req.email and req.email.lower() in details)):
            continue
        says_non_deletion = "non-deletion" in details or "not deletion" in details
        if says_non_deletion and claim:
            out.append({"activity_id": a.activity_id, "resource_id": a.resource_id,
                        "claim": "NON_DELETION", "classifier": "DELETION"})
    return out


def authorizes_deletion(text):
    """True only when the message actually asks for deletion of the account/data."""
    t = (text or "").lower()
    if any(marker in t for marker in NON_DELETION_MARKERS):
        return False
    if any(marker in t for marker in DELETION_MARKERS):
        return True
    # Multilingual fallback: require an account/data noun next to a removal verb.
    nouns = ("account", "profile", "data", "dados", "datos", "données", "konto",
             "cuenta", "conta", "アカウント", "계정")
    verbs = ("remov", "delete", "erase", "clos", "elimin", "supprim", "exclu",
             "削除", "삭제")
    return any(n in t for n in nouns) and any(v in t for v in verbs)
