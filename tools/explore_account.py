"""Step 2 exploration: how can an identifier-less account DELETE_RECORD row be
grounded to a subject? Prints, per sample case, the candidate account events in
the request window and the subject's already-linked resource events, so a general
rule can be mined (evaluation only; nothing here runs at prediction time).
"""
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker  # noqa: E402

data = loaders.load_all()
resolver = build_resolver(data.identities, data.audit_log)
built = agent.build_index()
linker = Linker(resolver, built["index"].facts, data.activity)

gold = {}
with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        gold[r["case_id"]] = json.loads(r["steps_json"])

reqs = {r.case_id: r for r in loaders.load_deletion_requests(
    ROOT / "dataset" / "sample_deletion_requests.csv")}

WORKER = ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "CHECK_DATA_FLOW",
          "CHECK_RESTORE_ACCESS", "BLOCK_RESTORE", "EXPORT_DATA")

for cid in sorted(gold):
    req = reqs[cid]
    gold_acct = [s["resource_id"] for s in gold[cid]
                 if s["system_id"] == "account_service"]
    uid = resolver.resolve(req.email)
    linked = linker.resources(uid)
    linked_ids = {rid for (_s, rid) in linked if not rid.startswith("account_")}

    win = [a for a in data.activity
           if req.requested_at <= a.event_time <= req.audit_at]

    def rel(t):
        """Signed seconds from reported_at (claim time)."""
        from datetime import datetime
        f = "%Y-%m-%dT%H:%M:%SZ"
        return (datetime.strptime(t, f) - datetime.strptime(req.reported_at, f)).total_seconds()

    cand = [a for a in win if a.event_type == "DELETE_RECORD"
            and a.target_system == "account_service"]
    linked_ev = [a for a in win if a.resource_id in linked_ids]

    print("=" * 110)
    print(f"{cid} audit={req.audit_at} req={req.requested_at} rep={req.reported_at} "
          f"claim={req.reported_status}")
    print(f"  GOLD acct: {gold_acct}   linked(other): {sorted(linked_ids)}")
    print(f"  candidates in window: {len(cand)}")
    for a in cand:
        mine = a.resource_id in gold_acct
        print(f"    {'>>' if mine else '  '} {a.activity_id} {a.event_time} "
              f"{a.resource_id:<14} {a.reported_result:<7} d_rep={rel(a.event_time):+9.0f}s"
              f"  {'|'.join([a.source_system, a.target_system])}")
    print(f"  subject-linked non-account events in window: {len(linked_ev)}")
    for a in linked_ev[:6]:
        print(f"        {a.activity_id} {a.event_time} {a.event_type:<18} "
              f"{a.resource_id:<16} d_rep={rel(a.event_time):+9.0f}s")
