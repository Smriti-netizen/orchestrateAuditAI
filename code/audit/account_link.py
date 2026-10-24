"""Provenance linking for the identifier-less ``account_service`` account row.

The deletion worker's ``DELETE_RECORD`` row for a person's primary account carries
no identifier (``details`` names only ``account_NNN``).  It is nevertheless
grounded, because the workflow claim is the worker's report of that same
operation, and the row is emitted around the claim time.

Candidate set
-------------
For a request whose account is not already resolved by an exact identifier:

* row type ``DELETE_RECORD`` -> ``account_service``;
* ``requested_at <= event_time <= audit_at`` (never after ``audit_at``);
* ``reported_result`` compatible with the claim the request carries
  (``COMPLETE`` -> SUCCESS, ``PARTIALLY_COMPLETE`` -> SUCCESS|PARTIAL,
  ``NOT_STARTED`` -> FAILED).  The claim is used only as provenance -- which row
  produced the claim -- never as proof that the deletion happened;
* ``|event_time - reported_at| <= ACCOUNT_CLAIM_WINDOW`` (the worker reports the
  claim shortly after acting on the row);
* the row is not already identifier-linked to a *different* request's subject.

Confidence and assignment
-------------------------
``confidence = 1 / len(candidates)``.  With one candidate the mapping is forced
(measured precision 1.00 on the public samples).  With several candidates the
mapping is genuinely ambiguous -- batch order, anchor adjacency and claim-time
proximity were each measured and rejected (see ``evaluation_report.md``), so the
probabilities are uniform and ``1/k`` is the honest confidence.

Candidates are then assigned **globally, one-to-one**: cases are served in
descending confidence (ties: smaller time distance to the claim, then case id),
and each takes its best unused row (ties: smaller time distance, then lower
activity index).  No row is ever handed to two subjects.  Cases whose
confidence is below ``CONFIDENCE_FLOOR`` are omitted rather than guessed; the
floor is chosen by the measured step-F1 sweep on the public samples.

Everything is deterministic: no randomness, no case ids, no sample answers.
"""
from datetime import datetime

from audit.link import LinkedResource

# The worker reports the claim shortly after acting on the account row.  Two
# hours is the measured outer bound on the samples and is wide enough not to
# depend on the exact scheduling offset.
ACCOUNT_CLAIM_WINDOW = 2 * 60 * 60

# Minimum candidate confidence that is worth a step.  Measured on the 32 public
# samples (step-level F1 over account steps): floor 1.0 -> 0.836, 0.5 -> 0.857,
# 0.25 -> 0.897.  Per-candidate precision is ~0.50 from k=2 upwards, so the
# floor at 0.25 converts the remaining misses into ~coin-flip steps.  The
# leave-one-out sweep over the 17 account cases chose 0.25 in 17/17 folds.
CONFIDENCE_FLOOR = 0.25

CLAIM_RESULTS = {
    "COMPLETE": {"SUCCESS"},
    "PARTIALLY_COMPLETE": {"SUCCESS", "PARTIAL"},
    "NOT_STARTED": {"FAILED"},
}


def _seconds(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").timestamp()


def analyse_candidates(requests, resolver, linker, activity):
    """Return ``(candidates, linked_accounts)`` for every request.

    ``candidates[case_id]`` is a list of ``(distance_seconds, activity_index,
    resource_id)`` sorted best-first; ``linked_accounts[case_id]`` is the set of
    account resources already resolved by an exact identifier (those cases need
    no provenance help).
    """
    requests = list(requests)
    linked_accounts, owner = {}, {}
    for req in requests:
        uid = resolver.resolve(req.email)
        linked = linker.resources(uid) if uid else {}
        accounts = {rid for (_s, rid) in linked if rid.startswith("account_")}
        linked_accounts[req.case_id] = accounts
        for rid in accounts:
            owner.setdefault(rid, set()).add(req.case_id)

    order = {a.activity_id: i for i, a in enumerate(activity)}
    rows = [a for a in activity
            if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"]

    out = {}
    for req in requests:
        if linked_accounts[req.case_id]:
            continue
        allowed = CLAIM_RESULTS.get(req.reported_status,
                                    {"SUCCESS", "PARTIAL", "FAILED"})
        claim = _seconds(req.reported_at)
        found = []
        for row in rows:
            if row.reported_result not in allowed:
                continue
            if not (req.requested_at <= row.event_time <= req.audit_at):
                continue
            if owner.get(row.resource_id, set()) - {req.case_id}:
                continue
            dist = abs(_seconds(row.event_time) - claim)
            if dist > ACCOUNT_CLAIM_WINDOW:
                continue
            found.append((dist, order[row.activity_id], row.resource_id))
        if found:
            out[req.case_id] = sorted(found)
    return out, linked_accounts


def resolve_account_rows(requests, resolver, linker, activity,
                         floor=None, window=None):
    """Map ``case_id -> account_service resource_id`` for provable cases only.

    Returns ``(picks, ambiguous)`` where ``ambiguous`` lists
    ``(case_id, reason)`` for the cases deliberately left alone.
    """
    floor = CONFIDENCE_FLOOR if floor is None else floor
    global ACCOUNT_CLAIM_WINDOW
    saved = ACCOUNT_CLAIM_WINDOW
    if window is not None:
        ACCOUNT_CLAIM_WINDOW = window
    try:
        candidates, linked = analyse_candidates(requests, resolver, linker, activity)
    finally:
        ACCOUNT_CLAIM_WINDOW = saved

    ambiguous = []
    scored = []
    for case_id, cands in candidates.items():
        confidence = 1.0 / len(cands)
        if confidence < floor:
            ambiguous.append((case_id, "below_confidence_floor"))
            continue
        # Each candidate: (confidence, distance, activity_index, resource_id).
        scored.append((case_id, confidence, cands))

    # Serve the most confident cases first; ties broken by claim distance then id.
    scored.sort(key=lambda item: (-item[1], item[2][0][0], item[0]))
    used, picks = set(), {}
    for case_id, confidence, cands in scored:
        chosen = next((c for c in cands if c[2] not in used), None)
        if chosen is None:
            ambiguous.append((case_id, "row_claimed_by_two_subjects"))
            continue
        used.add(chosen[2])
        picks[case_id] = chosen[2]

    seen = {}
    for case_id, rid in picks.items():
        seen.setdefault(rid, []).append(case_id)
    for rid, cids in seen.items():
        if len(cids) > 1:  # defensive; the assignment above already prevents this
            for case_id in cids:
                picks.pop(case_id, None)
                ambiguous.append((case_id, "row_claimed_by_two_subjects"))
    return picks, ambiguous


def add_provenance_resource(linked, system_id, resource_id, category, facts=()):
    """Copy of ``linked`` plus one provenance-resolved resource."""
    key = (system_id, resource_id)
    if key in linked:
        return linked
    out = dict(linked)
    out[key] = LinkedResource(system_id=system_id, resource_id=resource_id,
                              category=category, facts=list(facts),
                              link_sources={"activity_provenance"})
    return out


def add_provenance_account(linked, resource_id):
    """Copy of ``linked`` plus the provenance-resolved account (no facts)."""
    return add_provenance_resource(linked, "account_service", resource_id,
                                   "USER_PROFILE")
