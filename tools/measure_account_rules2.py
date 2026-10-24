"""Step 2 measurement, round 2: use the workflow claim only as PROVENANCE.

Observation from the 32 samples: the reported_result of the row that produced a
case's claim is consistent with that claim by construction
    COMPLETE            -> SUCCESS
    PARTIALLY_COMPLETE  -> SUCCESS or PARTIAL  (never FAILED)
    NOT_STARTED         -> FAILED
So the claim is used to filter WHICH activity row the claim came from. It is
still never used to decide the deletion state. Scored on the 17 cases whose
account has no identifier.
"""
import csv
import json
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
ORDER = {a.activity_id: i for i, a in enumerate(data.activity)}

gold = {}
with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        gold[r["case_id"]] = json.loads(r["steps_json"])
reqs = {r.case_id: r for r in loaders.load_deletion_requests(
    ROOT / "dataset" / "sample_deletion_requests.csv")}

CLAIM_OK = {
    "COMPLETE": {"SUCCESS"},
    "PARTIALLY_COMPLETE": {"SUCCESS", "PARTIAL"},
    "NOT_STARTED": {"FAILED"},
}


def secs(t):
    return datetime.strptime(t, FMT).timestamp()


def candidates(cid):
    req = reqs[cid]
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at]


def consistent(cid):
    ok = CLAIM_OK.get(reqs[cid].reported_status, {"SUCCESS", "PARTIAL", "FAILED"})
    return [a for a in candidates(cid) if a.reported_result in ok]


def anchors(cid):
    req = reqs[cid]
    uid = resolver.resolve(req.email)
    linked = {rid for (_s, rid) in linker.resources(uid)}
    return [a for a in data.activity
            if a.resource_id in linked and a.source_system == "deletion_worker"
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


def score(name, picker):
    picks = {cid: picker(cid) for cid, _ in needed()}
    # one account row may belong to only one subject: drop duplicated picks
    dup = {p for p in picks.values() if p and list(picks.values()).count(p) > 1}
    tp = fp = fn = amb = 0
    wrong = []
    for cid, want in needed():
        got = picks[cid]
        if got in dup:
            got = None
            amb += 1
        if got is None:
            fn += 1
            continue
        if got == want:
            tp += 1
        else:
            fp += 1
            wrong.append((cid, got, want))
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    print(f"{name:<46} tp={tp:<2} fp={fp:<2} fn={fn:<2} P={prec:.2f} R={rec:.2f} dup_dropped={amb}")
    for cid, got, want in wrong:
        print(f"      {cid}: got {got} want {want}")


def r8(cid):
    """Claim-consistent rows, nearest to the claim time."""
    cand = consistent(cid)
    if not cand:
        return None
    req = reqs[cid]
    cand = sorted(cand, key=lambda a: (abs(secs(a.event_time) - secs(req.reported_at)),
                                       ORDER[a.activity_id]))
    # ambiguity guard: require a clear 10-minute margin over the runner-up
    if len(cand) > 1 and abs(secs(cand[1].event_time) - secs(req.reported_at)) - \
            abs(secs(cand[0].event_time) - secs(req.reported_at)) < 600:
        return None
    return cand[0].resource_id


def r8b(cid):
    """Claim-consistent rows, nearest to the claim time, no margin guard."""
    cand = consistent(cid)
    if not cand:
        return None
    req = reqs[cid]
    cand = sorted(cand, key=lambda a: (abs(secs(a.event_time) - secs(req.reported_at)),
                                       ORDER[a.activity_id]))
    return cand[0].resource_id


def r9(cid):
    """Claim-consistent rows, nearest own worker event."""
    cand = consistent(cid)
    an = anchors(cid)
    if not cand or not an:
        return None
    cand = sorted(cand, key=lambda a: (min(abs(ORDER[a.activity_id] - ORDER[b.activity_id])
                                           for b in an), ORDER[a.activity_id]))
    return cand[0].resource_id


def r10(cid):
    """Claim-consistent rows that precede the subject's first worker event."""
    cand = consistent(cid)
    an = anchors(cid)
    if not cand or not an:
        return None
    first = min(ORDER[b.activity_id] for b in an)
    before = [a for a in cand if ORDER[a.activity_id] < first]
    if not before:
        return None
    return max(before, key=lambda a: ORDER[a.activity_id]).resource_id


print("needed:", len(needed()))
score("R8  claim-consistent + nearest claim time + 10min margin", r8)
score("R8b claim-consistent + nearest claim time", r8b)
score("R9  claim-consistent + nearest own worker event", r9)
score("R10 claim-consistent + latest before own first worker event", r10)

print("\nper-case detail (claim-consistent rows only):")
for cid, want in needed():
    req = reqs[cid]
    cand = sorted(consistent(cid), key=lambda a: ORDER[a.activity_id])
    near = sorted(cand, key=lambda a: abs(secs(a.event_time) - secs(req.reported_at)))[:3]
    print(f"  {cid} claim={req.reported_status:<20} want={want:<13} "
          f"consistent_rows={len(cand)}")
    for a in near:
        print(f"      {a.activity_id} {a.resource_id:<13} {a.reported_result:<8} "
              f"d_rep={secs(a.event_time) - secs(req.reported_at):+8.0f}s "
              f"{'<< GOLD' if a.resource_id == want else ''}")
