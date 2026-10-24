"""Step 2 diagnostic: for each sample case that needs the activity rule, show the
candidates nearest to the claim time and their wording shape, so a general rule can
be stated. Evaluation only.
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


for cid in sorted(gold):
    req = reqs[cid]
    acct = [s["resource_id"] for s in gold[cid] if s["system_id"] == "account_service"]
    if not acct:
        continue
    uid = resolver.resolve(req.email)
    linked = linker.resources(uid)
    accts = {rid for (s, rid) in linked if rid.startswith("account_")}
    if accts:
        continue  # identifiers already solve it
    cand = [a for a in data.activity
            if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"
            and req.requested_at <= a.event_time <= req.audit_at]
    cand.sort(key=lambda a: abs(secs(a.event_time) - secs(req.reported_at)))
    others = [a for a in cand if a.resource_id not in acct]
    print("=" * 108)
    print(f"{cid} gold={acct[0]} rep={req.reported_at} audit={req.audit_at} "
          f"linked_other={len([r for (s, r) in linked if not r.startswith('account_')])}")
    for a in cand[:4]:
        d = secs(a.event_time) - secs(req.reported_at)
        mark = ">>" if a.resource_id in acct else "  "
        print(f"  {mark} {a.activity_id} d={d:+8.0f}s {a.resource_id:<13} "
              f"{a.reported_result:<8} {a.details[:92]}")
    if not any(a.resource_id in acct for a in cand[:4]):
        for a in cand:
            if a.resource_id in acct:
                d = secs(a.event_time) - secs(req.reported_at)
                print(f"  >> {a.activity_id} d={d:+8.0f}s {a.resource_id:<13} "
                      f"{a.reported_result:<8} {a.details[:92]}  (further)")
                break
