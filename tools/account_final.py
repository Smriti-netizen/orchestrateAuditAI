"""Step 2 finalisation: the account-activity rule + a wrong-pick guard.

Rule (candidate, general):
  A) anchors = this subject's identifier-linked resources that have a
     deletion_worker activity row inside [requested_at, audit_at].
  B) candidates = account_service DELETE_RECORD rows inside the same window whose
     reported_result is consistent with the workflow claim.  The claim is used
     only as PROVENANCE (which row the claim came from), never as proof of state:
        COMPLETE           -> SUCCESS
        PARTIALLY_COMPLETE -> SUCCESS | PARTIAL
        NOT_STARTED        -> FAILED
  C) pick the unique candidate at activity order position (first_anchor - 1)
     that is also the candidate nearest reported_at.
  D) guard: if the same account row would be picked for two different requests,
     both are dropped (one physical row cannot be two people).
  E) guard: never pick a row that identifiers already link to another subject.

Measured on the 17 sample cases whose account has no identifier; then applied to
all 518 requests for plausibility (coverage, duplicates, ambiguity).
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


def secs(t):
    return datetime.strptime(t, FMT).timestamp()


data = loaders.load_all()
resolver = build_resolver(data.identities, data.audit_log)
linker = Linker(resolver, agent.build_index()["index"].facts, data.activity)
ORDER = {a.activity_id: i for i, a in enumerate(data.activity)}
BY_ID = {a.activity_id: a for a in data.activity}

CLAIM_OK = {"COMPLETE": {"SUCCESS"},
            "PARTIALLY_COMPLETE": {"SUCCESS", "PARTIAL"},
            "NOT_STARTED": {"FAILED"}}

gold = {}
with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        gold[r["case_id"]] = json.loads(r["steps_json"])

SAMPLE = {r.case_id: r for r in loaders.load_deletion_requests(
    ROOT / "dataset" / "sample_deletion_requests.csv")}
ALL = {r.case_id: r for r in loaders.load_deletion_requests(
    ROOT / "dataset" / "deletion_requests.csv")}


def linked_resources(req):
    uid = resolver.resolve(req.email)
    return uid, linker.resources(uid) if uid else {}


def anchors(req, linked):
    ids = {rid for (_s, rid) in linked}
    return [a for a in data.activity
            if a.resource_id in ids and a.source_system == "deletion_worker"
            and req.requested_at <= a.event_time <= req.audit_at]


def candidates(req):
    ok = CLAIM_OK.get(req.reported_status, {"SUCCESS", "PARTIAL", "FAILED"})
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD"
            and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at
            and a.reported_result in ok]


def raw_pick(req):
    """Return (activity or None, reason). No cross-case guards yet."""
    uid, linked = linked_resources(req)
    acct = [(s, rid) for (s, rid) in linked if rid.startswith("account_")]
    if acct:
        return None, "already-identifier-linked"
    an = anchors(req, linked)
    if not an:
        return None, "no-anchor"
    first = min(ORDER[a.activity_id] for a in an)
    adj = [a for a in candidates(req) if ORDER[a.activity_id] == first - 1]
    if len(adj) != 1:
        return None, "adjacent-ambiguous" if adj else "no-adjacent"
    cand = candidates(req)
    near = min(cand, key=lambda a: (abs(secs(a.event_time) - secs(req.reported_at)),
                                    ORDER[a.activity_id]))
    if near.activity_id != adj[0].activity_id:
        return None, "claim-time-disagrees"
    return adj[0], "ok"


# identifier ownership: account row -> subject, from identifiers only
OWNER = {}
for cid, req in ALL.items():
    uid, linked = linked_resources(req)
    for (s, rid) in linked:
        if rid.startswith("account_"):
            OWNER.setdefault(rid, set()).add(cid)


def decide(requests, use_dup_guard=True):
    picks = {}
    reasons = {}
    for cid, req in requests.items():
        act, why = raw_pick(req)
        picks[cid] = act.resource_id if act else None
        reasons[cid] = why
    if use_dup_guard:
        for cid, rid in list(picks.items()):
            if not rid:
                continue
            owners = OWNER.get(rid, set())
            if owners - {cid}:
                picks[cid] = None
                reasons[cid] = "owned-by-other-subject"
        counts = {}
        for rid in picks.values():
            if rid:
                counts[rid] = counts.get(rid, 0) + 1
        for cid, rid in list(picks.items()):
            if rid and counts[rid] > 1:
                picks[cid] = None
                reasons[cid] = "duplicate-across-cases"
    return picks, reasons


def score(requests, picks, label):
    need = []
    for cid in sorted(requests):
        acct = [s["resource_id"] for s in gold.get(cid, [])
                if s["system_id"] == "account_service"]
        if not acct:
            continue
        if picks.get(cid) is None and reasons.get(cid) == "already-identifier-linked":
            continue
        need.append((cid, acct[0]))
    tp = fp = fn = 0
    wrong = []
    for cid, want in need:
        got = picks.get(cid)
        if got is None:
            fn += 1
        elif got == want:
            tp += 1
        else:
            fp += 1
            wrong.append((cid, got, want))
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    print(f"{label:<44} tp={tp:<2} fp={fp:<2} fn={fn:<2} P={p:.2f} R={r:.2f}")
    for cid, got, want in wrong:
        print(f"      WRONG {cid}: got {got} want {want}")
    return tp, fp, fn


print("=" * 100)
print("A. SAMPLE: precision / recall of the account rule (17 cases needing it)")
print("=" * 100)
picks, reasons = decide(SAMPLE, use_dup_guard=False)
score(SAMPLE, picks, "no guards")
picks, reasons = decide(SAMPLE, use_dup_guard=True)
score(SAMPLE, picks, "duplicate + identifier-owner guards")
print()
for cid in sorted(SAMPLE):
    acct = [s["resource_id"] for s in gold[cid] if s["system_id"] == "account_service"]
    print(f"  {cid} gold={acct[0]:<14} pick={str(picks.get(cid)):<14} {reasons.get(cid)}")

print()
print("=" * 100)
print("B. ALL 518: plausibility (coverage / duplicates / ambiguity)")
print("=" * 100)
picks, reasons = decide(ALL, use_dup_guard=True)
from collections import Counter
c = Counter(reasons.values())
for k, v in c.most_common():
    print(f"  {k:<26} {v}")
got = [cid for cid, r in picks.items() if r]
print(f"  -> cases receiving an account resource from the rule: {len(got)}")
print(f"  -> distinct account rows: {len({picks[c] for c in got})}")
print(f"  -> identifier-linked account cases: {sum(1 for r in reasons.values() if r == 'already-identifier-linked')}")
print(f"  -> rule-covered cases by family:")
fam = Counter(picks[c].rsplit("_", 1)[0] for c in got)
print("     ", dict(fam))
if len(got) <= 25:
    for cid in sorted(got):
        print(f"     {cid} -> {picks[cid]}")
