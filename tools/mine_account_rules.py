"""Step 2 mining: candidate rules for grounding an identifier-less account
DELETE_RECORD row, measured for precision/recall on the 32 public samples.

Rules are expressed as general features only (no case ids, no account_00N<User>
convention). Run: py tools/mine_account_rules.py
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


def secs(t):
    return datetime.strptime(t, FMT).timestamp()


def candidates(req):
    return [a for a in data.activity
            if a.event_type == "DELETE_RECORD"
            and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at]


def templates(act_rows):
    """Bucket the details wording so the rule can be stated generally."""
    out = {}
    for a in act_rows:
        # Replace the id so wording shape, not identity, is compared.
        shape = re.sub(r"account_\d+", "account_X", a.details)
        out[a.activity_id] = shape
    return out


need = []
for cid in sorted(gold):
    req = reqs[cid]
    acct_steps = [s["resource_id"] for s in gold[cid] if s["system_id"] == "account_service"]
    uid = resolver.resolve(req.email)
    linked_accts = {rid for (s, rid) in linker.resources(uid) if s.startswith("account_")}
    already = bool(linked_accts)
    need.append((cid, req, acct_steps, already))

print(f"cases with a gold account step: {sum(1 for _, _, a, _ in need if a)}")
print(f"cases where an account is already identifier-linked: {sum(1 for _, _, a, e in need if a and e)}")
print(f"cases that NEED the activity rule: {sum(1 for _, _, a, e in need if a and not e)}")

# Collect the exact details wordings so the rule is not case-specific.
all_cands = []
for cid, req, acct, already in need:
    for a in candidates(req):
        all_cands.append((cid, req, acct, a))
shapes = {}
for cid, req, acct, a in all_cands:
    shapes.setdefault(re.sub(r"account_\d+", "account_X", a.details), []).append(
        (cid, a.resource_id in acct))
print("\ndetails wording shapes across all account DELETE_RECORD candidates:")
for shape, hits in sorted(shapes.items(), key=lambda kv: -len(kv[1])):
    gold_hits = sum(1 for c, g in hits if g)
    print(f"  n={len(hits):<4} gold={gold_hits:<3} {shape[:110]}")


def evaluate(name, picker):
    tp = fp = fn = 0
    bad = []
    for cid, req, acct, already in need:
        if not acct or already:
            continue  # rule is only needed where identifiers cannot help
        choice = picker(cid, req, acct)
        if choice is None:
            fn += 1
            bad.append((cid, "none", acct))
        elif choice.resource_id in acct:
            tp += 1
        else:
            fp += 1
            bad.append((cid, choice.resource_id, acct))
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    print(f"\n{name}: tp={tp} fp={fp} fn={fn} precision={prec:.3f} recall={rec:.3f}")
    for cid, got, exp in bad:
        print(f"    wrong {cid}: chose {got} expected {exp}")
    return tp, fp, fn


def r_closest_reported(cid, req, acct):
    cand = candidates(req)
    if not cand:
        return None
    return min(cand, key=lambda a: abs(secs(a.event_time) - secs(req.reported_at)))


def r_reported_wording(cid, req, acct):
    cand = [a for a in candidates(req)
            if "reported the requested operation" in a.details.lower()]
    if len(cand) == 1:
        return cand[0]
    return None


def r_wording_then_closest(cid, req, acct):
    cand = candidates(req)
    if not cand:
        return None
    scored = []
    for a in cand:
        d = abs(secs(a.event_time) - secs(req.reported_at))
        explicit = "reported the requested operation" in a.details.lower()
        scored.append((not explicit, d, a.activity_id, a))
    scored.sort(key=lambda t: t[:3])
    return scored[0][3]


def r_window_before_reported(cid, req, acct):
    """Only rows in [requested_at, reported_at] if the claim predates audit."""
    if not (req.requested_at <= req.reported_at <= req.audit_at):
        return None
    cand = [a for a in candidates(req) if a.event_time <= req.reported_at]
    if not cand:
        return None
    return max(cand, key=lambda a: (a.event_time, a.activity_id))


evaluate("R1 closest candidate to reported_at", r_closest_reported)
evaluate("R2 details='reported the requested operation' and unique", r_reported_wording)
evaluate("R3 explicit wording first, then closest to reported_at", r_wording_then_closest)
evaluate("R4 latest candidate at/before reported_at", r_window_before_reported)
