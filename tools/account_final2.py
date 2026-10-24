"""Step 2, round 4: batch-aware account rule.

Insight from the diagnostics: within one reported_at batch the deletion worker
submits several account rows minutes apart, and the mapping row->case inside the
batch is not recoverable from any deterministic signal (both cases in a batch can
even share reported_result).  When a request is ALONE at its reported_at, the
only account row that can have produced the claim is the one a few minutes
before/after it.

Rule S(W): if reported_at is unique in the input request set, take the
claim-consistent account_service DELETE_RECORD rows with
|event_time - reported_at| <= W; if exactly one, use it.  Guards: the row must
not be identifier-linked to another subject in the same input set, and must not
be chosen for two subjects.

Scored on the 17 sample cases that need it; W is swept to show the trade-off
(no value is hardcoded into the engine until the precision is measured).
"""
import csv
import json
import sys
from collections import Counter, defaultdict
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


def linked(req):
    uid = resolver.resolve(req.email)
    return linker.resources(uid) if uid else {}


def claim_rows(req):
    ok = CLAIM_OK.get(req.reported_status, {"SUCCESS", "PARTIAL", "FAILED"})
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD"
            and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at
            and a.reported_result in ok]


def run(requests, W, mode):
    """mode 'singleton' | 'all'."""
    batch = Counter(r.reported_at for r in requests.values())
    owner = {}
    for cid, r in requests.items():
        for (_s, rid) in linked(r):
            if rid.startswith("account_"):
                owner.setdefault(rid, set()).add(cid)
    picks, why = {}, {}
    for cid, r in requests.items():
        if any(rid.startswith("account_") for (_s, rid) in linked(r)):
            picks[cid] = None
            why[cid] = "identifier"
            continue
        if mode == "singleton" and batch[r.reported_at] > 1:
            picks[cid] = None
            why[cid] = "shared-claim-time"
            continue
        win = [a for a in claim_rows(r)
               if abs(secs(a.event_time) - secs(r.reported_at)) <= W]
        if not win:
            picks[cid] = None
            why[cid] = "no-row-in-window"
        elif len(win) > 1:
            picks[cid] = None
            why[cid] = "multiple-rows-in-window"
        else:
            picks[cid] = win[0].resource_id
            why[cid] = "ok"
    # ownership + duplicate guards
    for cid, rid in list(picks.items()):
        if not rid:
            continue
        if owner.get(rid, set()) - {cid}:
            picks[cid] = None
            why[cid] = "owned-by-other"
    cnt = Counter(v for v in picks.values() if v)
    for cid, rid in list(picks.items()):
        if rid and cnt[rid] > 1:
            picks[cid] = None
            why[cid] = "duplicate"
    return picks, why


def score(requests, picks, why):
    need = []
    for cid in sorted(requests):
        g = [s["resource_id"] for s in gold.get(cid, [])
             if s["system_id"] == "account_service"]
        if g and why.get(cid) != "identifier":
            need.append((cid, g[0]))
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
    return tp, fp, fn, p, r, wrong


print("SINGLETON-batch rule, window sweep (sample = 32 requests):")
print(f"{'W(s)':>7} {'tp':>3} {'fp':>3} {'fn':>3} {'P':>6} {'R':>6}   wrong")
for W in (300, 600, 900, 1800, 3600, 5000, 7200, 10800, 21600, 86400):
    picks, why = run(SAMPLE, W, "singleton")
    tp, fp, fn, p, r, wrong = score(SAMPLE, picks, why)
    print(f"{W:>7} {tp:>3} {fp:>3} {fn:>3} {p:>6.2f} {r:>6.2f}   "
          + ", ".join(f"{c}:{g}/{w}" for c, g, w in wrong))

print()
print("detail at W=5000 and W=7200:")
for W in (5000, 7200):
    picks, why = run(SAMPLE, W, "singleton")
    print(f"  W={W}")
    for cid in sorted(SAMPLE):
        g = [s["resource_id"] for s in gold.get(cid, [])
             if s["system_id"] == "account_service"]
        if not g:
            continue
        flag = ("ok" if picks.get(cid) == g[0] else
                ("omit" if picks.get(cid) is None else "WRONG"))
        print(f"    {cid} {flag:<5} pick={str(picks.get(cid)):<14} gold={g[0]:<14} {why.get(cid)}")

print()
print("ALL 518: how many requests are alone at their reported_at, and how many the rule covers")
for W in (5000, 7200):
    picks, why = run(ALL, W, "singleton")
    c = Counter(why.values())
    print(f"  W={W}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    print(f"         picks={sum(1 for v in picks.values() if v)} distinct={len({v for v in picks.values() if v})}")
    b = Counter(r.reported_at for r in ALL.values())
    print(f"         singleton cases={sum(1 for r in ALL.values() if b[r.reported_at] == 1)}, "
          f"batches={len(set(r.reported_at for r in ALL.values()))}")
