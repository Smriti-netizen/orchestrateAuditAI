"""Step 2 measurement: candidate account-grounding rules, scored on the 32 samples.

Only the 17 cases whose account has no identifier are scored (the rest are already
solved by exact identifiers). Rules use only: activity ids/order, activity times,
event types, reported_result, and the subject's already-linked resources.
"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"
data = loaders.load_all()
resolver = build_resolver(data.identities, data.audit_log)
linker = Linker(resolver, agent.build_index()["index"].facts, data.activity)

gold = {}
with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        gold[r["case_id"]] = json.loads(r["steps_json"])
reqs = {r.case_id: r for r in loaders.load_deletion_requests(
    ROOT / "dataset" / "sample_deletion_requests.csv")}

# Global activity order (file order is the worker's submission order).
ORDER = {a.activity_id: i for i, a in enumerate(data.activity)}
row = {a.activity_id: a for a in data.activity}
WORKER_EVENTS = ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
                 "RETAIN_RECORD", "EXPORT_DATA", "CHECK_DATA_FLOW",
                 "CHECK_RESTORE_ACCESS", "BLOCK_RESTORE")


def secs(t):
    return datetime.strptime(t, FMT).timestamp()


def anchors(cid):
    """Identifier-linked resources of this subject that have deletion_worker rows."""
    req = reqs[cid]
    uid = resolver.resolve(req.email)
    linked = {rid for (_s, rid) in linker.resources(uid)}
    out = []
    for a in data.activity:
        if a.resource_id in linked and a.source_system == "deletion_worker" \
                and req.requested_at <= a.event_time <= req.audit_at:
            out.append(a)
    return out


def candidates(cid):
    req = reqs[cid]
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at]


def needed():
    out = []
    for cid in sorted(gold):
        acct = [s["resource_id"] for s in gold[cid] if s["system_id"] == "account_service"]
        if not acct:
            continue
        uid = resolver.resolve(reqs[cid].email)
        if any(rid.startswith("account_") for (_s, rid) in linker.resources(uid)):
            continue
        out.append((cid, acct[0]))
    return out


def evaluate(name, picker, verbose=True):
    tp = fp = fn = 0
    wrong = []
    for cid, want in needed():
        got = picker(cid)
        if got is None:
            fn += 1
            if verbose:
                wrong.append((cid, "OMITTED(ambiguous)", want))
        elif got == want:
            tp += 1
        else:
            fp += 1
            wrong.append((cid, got, want))
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    print(f"{name:<52} tp={tp:<2} fp={fp:<2} fn={fn:<2} P={prec:.2f} R={rec:.2f}")
    if verbose:
        for cid, got, want in wrong:
            print(f"      {cid}: got {got} want {want}")
    return tp, fp, fn


# ---------------- rules ------------------------------------------------------
def r5_nearest_anchor(cid, margin=0):
    """Nearest account row to this subject's own worker events (by activity index)."""
    an = anchors(cid)
    cand = candidates(cid)
    if not an or not cand:
        return None
    scored = sorted(cand, key=lambda a: (min(abs(ORDER[a.activity_id] - ORDER[b.activity_id])
                                             for b in an), ORDER[a.activity_id]))
    if margin and len(scored) > 1:
        d0 = min(abs(ORDER[scored[0].activity_id] - ORDER[b.activity_id]) for b in an)
        d1 = min(abs(ORDER[scored[1].activity_id] - ORDER[b.activity_id]) for b in an)
        if d1 - d0 < margin:
            return None
    return scored[0].resource_id


def r6_anchor_then_claim_gap(cid):
    """Nearest anchor; tie-break by |time - reported_at|; omit if the two signals differ."""
    an = anchors(cid)
    cand = candidates(cid)
    if not an or not cand:
        return None
    req = reqs[cid]
    by_anchor = sorted(cand, key=lambda a: (min(abs(ORDER[a.activity_id] - ORDER[b.activity_id])
                                                 for b in an), ORDER[a.activity_id]))
    by_time = sorted(cand, key=lambda a: (abs(secs(a.event_time) - secs(req.reported_at)),
                                          ORDER[a.activity_id]))
    if by_anchor[0].activity_id != by_time[0].activity_id:
        return None
    return by_anchor[0].resource_id


def r7_fifo(cid):
    """FIFO: among cases sharing a reported_at, account rows are consumed in
    requested_at order. Deterministic global assignment, then uniqueness check."""
    req = reqs[cid]
    batch = [c for c in reqs.values() if c.reported_at == req.reported_at]
    batch.sort(key=lambda c: (c.requested_at, c.case_id))
    pool = sorted(candidates(cid), key=lambda a: ORDER[a.activity_id])
    # Candidates unique to this reported_at batch: rows whose time is within the
    # batch's own run (before the claim plus a small grace).
    pool = [a for a in pool
            if secs(a.event_time) <= secs(req.reported_at) + 900]
    if len(pool) < len(batch):
        return None
    pool = pool[-len(batch):]
    idx = [c.case_id for c in batch].index(cid)
    return pool[idx].resource_id


print("needed cases (account has no identifier):", len(needed()))
evaluate("R5 nearest own worker event, no margin", lambda c: r5_nearest_anchor(c, 0))
evaluate("R5b nearest own worker event, omit if tie (<2 gap)", lambda c: r5_nearest_anchor(c, 2))
evaluate("R5c nearest own worker event, omit if tie (<4 gap)", lambda c: r5_nearest_anchor(c, 4))
evaluate("R6 nearest anchor AND nearest claim-time must agree", r6_anchor_then_claim_gap)
evaluate("R7 FIFO within reported_at batch", r7_fifo)

print("\nper-case evidence for the best rule (R6):")
for cid, want in needed():
    an = anchors(cid)
    req = reqs[cid]
    cand = sorted(candidates(cid),
                  key=lambda a: min(abs(ORDER[a.activity_id] - ORDER[b.activity_id])
                                    for b in an) if an else 10 ** 9)
    print(f"  {cid} want={want:<13} anchors={[(b.activity_id, b.resource_id) for b in an][:3]}")
    for a in cand[:4]:
        d = min(abs(ORDER[a.activity_id] - ORDER[b.activity_id]) for b in an) if an else "-"
        print(f"      {a.activity_id} {a.resource_id:<13} dist={d!s:<5} "
              f"d_rep={secs(a.event_time) - secs(req.reported_at):+8.0f}s "
              f"{a.reported_result}")
