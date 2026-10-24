"""Step 2 measurement, round 3: precision/recall sweep for an anchor-distance rule.

Rule family R(k): consider claim-consistent account rows that come BEFORE the
subject's first own worker event and within k activity steps of it. Pick the one
nearest that event, but only if it is unique at that distance (otherwise omit).
Also reports the cheap degenerations (k = infinity) for comparison.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker  # noqa: E402

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

CLAIM_OK = {"COMPLETE": {"SUCCESS"},
            "PARTIALLY_COMPLETE": {"SUCCESS", "PARTIAL"},
            "NOT_STARTED": {"FAILED"}}


def consistent(cid):
    req = reqs[cid]
    ok = CLAIM_OK.get(req.reported_status, {"SUCCESS", "PARTIAL", "FAILED"})
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at
            and a.reported_result in ok]


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


def pick(cid, k):
    an = anchors(cid)
    if not an:
        return None
    first = min(ORDER[b.activity_id] for b in an)
    cand = [a for a in consistent(cid)
            if ORDER[a.activity_id] < first and first - ORDER[a.activity_id] <= k]
    if not cand:
        return None
    cand.sort(key=lambda a: (first - ORDER[a.activity_id], ORDER[a.activity_id]))
    d0 = first - ORDER[cand[0].activity_id]
    if len(cand) > 1 and (first - ORDER[cand[1].activity_id]) == d0:
        return None  # tie at the nearest distance -> ambiguous
    return cand[0].resource_id


print(f"{'k':>3}  {'tp':>3} {'fp':>3} {'fn':>3}  precision  recall")
for k in (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 10 ** 6):
    tp = fp = fn = 0
    for cid, want in needed():
        got = pick(cid, k)
        if got is None:
            fn += 1
        elif got == want:
            tp += 1
        else:
            fp += 1
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    print(f"{k:>7}  {tp:>3} {fp:>3} {fn:>3}  {p:>8.2f}  {r:>6.2f}")

print("\ndetail at k=6 and k=12:")
for k in (6, 12):
    print(f" k={k}")
    for cid, want in needed():
        got = pick(cid, k)
        flag = "ok" if got == want else ("omit" if got is None else "WRONG")
        print(f"   {cid} {flag:<5} got={got} want={want}")
