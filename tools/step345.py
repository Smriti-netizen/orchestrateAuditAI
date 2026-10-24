"""Steps 3-5 measurements against the 32 public samples (evaluation only).

A. Step 3 - for every missed analytics_* / audit_record_* step, list every
   appearance in the evidence index, the file capture time, admissibility at
   audit_at, and whether that appearance carries a subject identifier.
B. Step 4 - evidence-id hit rate for the cited evidence, split by phase and by
   system, plus every remaining divergent citation.
C. Step 5 - operation-sequence diff for the failing cases: missing operations,
   extra operations, and order inversions.
"""
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402

PRED = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    os.environ.get("OPENCODE_TMP", r"C:\Users\smrit\AppData\Local\Temp\opencode")) / "sample_pred.csv"


def load(path, key):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {r[key]: r for r in csv.DictReader(fh)}


gold = load(ROOT / "dataset" / "sample_output.csv", "case_id")
pred = load(PRED, "case_id")
reqs = load(ROOT / "dataset" / "sample_deletion_requests.csv", "case_id")

built = agent.build_index()
facts = built["index"].facts
by_res = defaultdict(list)
for f in facts:
    by_res[f.resource_id].append(f)

IDENT_KEYS = ("user_ref", "email", "phone", "email_hash", "customer_reference",
              "source_resource_id")


def key(step):
    return (step["phase"], step["operation"], step["system_id"], step["resource_id"])


print("=" * 112)
print("A. STEP 3 - missed analytics/audit_record steps: every appearance and why it cannot be linked")
print("=" * 112)
print(f"{'case':<8} {'resource':<18} {'audit_at':<21} {'file':<11} {'captured':<21} "
      f"{'admissible':<11} identifiers")
for cid in sorted(gold):
    audit_at = reqs[cid]["audit_at"]
    pkeys = {key(s) for s in json.loads(pred[cid]["steps_json"])}
    for s in json.loads(gold[cid]["steps_json"]):
        if key(s) in pkeys:
            continue
        rid = s["resource_id"]
        if not rid.startswith(("analytics_", "audit_record_", "tombstone_")):
            continue
        seen = by_res.get(rid, [])
        if not seen:
            print(f"{cid:<8} {rid:<18} {audit_at:<21} {'-':<11} {'-':<21} {'-':<11} no fact at all")
        for f in sorted(seen, key=lambda f: (f.captured_at, f.file_id)):
            ids = {k: str((f.identifiers or {}).get(k) or "").strip()
                   for k in IDENT_KEYS}
            ids = {k: v for k, v in ids.items() if v}
            file_time = (f.extra or {}).get("file_captured_at") or f.captured_at
            ok = file_time <= audit_at and f.captured_at <= audit_at
            print(f"{cid:<8} {rid:<18} {audit_at:<21} {f.file_id:<11} "
                  f"{(f.extra or {}).get('file_captured_at') or f.captured_at:<21} "
                  f"{('YES' if ok else 'no'):<11} {ids or '-'}")

print()
print("=" * 112)
print("B. STEP 4 - evidence-id hit rate on the steps we do predict")
print("=" * 112)
hit = Counter()
tot = Counter()
hit_sys = Counter()
tot_sys = Counter()
divergent = []
for cid in sorted(gold):
    gsteps = json.loads(gold[cid]["steps_json"])
    psteps = json.loads(pred[cid]["steps_json"])
    pmap = {key(s): s for s in psteps}
    for g in gsteps:
        tot[g["phase"]] += 1
        tot_sys[g["system_id"]] += 1
        p = pmap.get(key(g))
        if p is None:
            continue
        if p["evidence_id"] == g["evidence_id"]:
            hit[g["phase"]] += 1
            hit_sys[g["system_id"]] += 1
        else:
            divergent.append((cid, g["phase"], g["resource_id"], g["evidence_id"],
                              p["evidence_id"], g["system_id"]))
print(f"{'phase':<14} {'cited ok':>9} {'matched':>9}  rate")
for ph in ("FINDING", "REMEDIATION", "VERIFICATION"):
    print(f"{ph:<14} {hit[ph]:>9} {tot[ph]:>9}  {hit[ph] / tot[ph]:.1%}" if tot[ph] else f"{ph}: n/a")
print()
print(f"{'system':<22} {'cited ok':>9} {'matched':>9}  rate")
for sid in sorted(tot_sys):
    print(f"{sid:<22} {hit_sys[sid]:>9} {tot_sys[sid]:>9}  {hit_sys[sid] / tot_sys[sid]:.1%}")
print()
print(f"divergent citations: {len(divergent)}")
for row in divergent:
    print("   ", row)
print()
print("empty-evidence convention check (incomplete remediation / post-remediation checks):")
empty_ok = empty_tot = 0
for cid in sorted(gold):
    status = gold[cid]["deletion_status"]
    for g in json.loads(gold[cid]["steps_json"]):
        p = {key(s): s for s in json.loads(pred[cid]["steps_json"])}.get(key(g))
        if p is None:
            continue
        if g["phase"] == "REMEDIATION" or status == "INCOMPLETE":
            empty_tot += 1
            empty_ok += (g["evidence_id"] == "" and p["evidence_id"] == "")
print(f"   {empty_ok}/{empty_tot}")

print()
print("=" * 112)
print("C. STEP 5 - operation-sequence diff for failing cases")
print("=" * 112)
print(f"{'case':<8} {'gold':>4} {'pred':>4} {'missing ops':<60} {'extra ops':<20} inversions")
for cid in sorted(gold):
    gops = [(s["phase"], s["operation"], s["resource_id"])
            for s in json.loads(gold[cid]["steps_json"])]
    pops = [(s["phase"], s["operation"], s["resource_id"])
            for s in json.loads(pred[cid]["steps_json"])]
    if gops == pops:
        continue
    miss = [f"{o}@{r}" for (ph, o, r) in gops if (ph, o, r) not in pops]
    extra = [f"{o}@{r}" for (ph, o, r) in pops if (ph, o, r) not in gops]
    # is the predicted sequence a subsequence of gold? count skipped gold ops only
    gi = 0
    skipped = 0
    for p in pops:
        while gi < len(gops) and gops[gi] != p:
            gi += 1
            skipped += 1
        if gi < len(gops):
            gi += 1
    subseq = skipped == len(miss)
    print(f"{cid:<8} {len(gops):>4} {len(pops):>4} "
          f"{(', '.join(miss) or '-')[:60]:<60} {(', '.join(extra) or '-')[:20]:<20} "
          f"{'none' if subseq else 'YES'}")
