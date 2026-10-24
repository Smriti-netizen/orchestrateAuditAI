"""Step 1: exhaustive miss breakdown for the 32 sample cases (evaluation only).

For every gold step we ask: did we predict the same (phase, operation, system,
resource)? If not, where does that resource_id appear anywhere under dataset/,
and does that appearance carry an identifier? Groups misses by cause.

Usage: py tools/breakdown.py [predicted.csv]
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.identity import build_resolver  # noqa: E402

PRED = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    os.environ.get("TMP", r"C:\Users\smrit\AppData\Local\Temp\opencode")) / "sample_pred.csv"


def load_csv(path, key):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {r[key]: r for r in csv.DictReader(fh)}


gold = load_csv(ROOT / "dataset" / "sample_output.csv", "case_id")
pred = load_csv(PRED, "case_id")
req = load_csv(ROOT / "dataset" / "sample_deletion_requests.csv", "case_id")

data = loaders.load_all()
built = agent.build_index()
facts = built["index"].facts
resolver = build_resolver(data.identities, data.audit_log)

# --- raw text of every textual evidence file --------------------------------
manifest = load_csv(ROOT / "dataset" / "evidence_files.csv", "file_id")
ev_text = {}
for fid, row in manifest.items():
    p = ROOT / "dataset" / row["file_path"]
    if p.suffix.lower() in (".sql", ".json", ".log", ".txt"):
        ev_text[fid] = p.read_text(encoding="utf-8", errors="replace").splitlines()

ACT_BY_RES = defaultdict(list)
for a in data.activity:
    ACT_BY_RES[a.resource_id].append(a)


def ident_in_text(text, uid):
    ids = resolver.user_identifier_set(uid) if uid else None
    if not ids:
        return False
    t = text.lower()
    if ids["user_id"] and ids["user_id"].lower() in t:
        return True
    if any(e and e in t for e in ids["emails"]):
        return True
    flat = re.sub(r"\D", "", t)
    if any(p and p in flat for p in ids["phones"]):
        return True
    if any(h and h in t for h in ids["hashes"]):
        return True
    return False


def fact_has_ident(rid):
    for f in facts:
        if f.resource_id == rid:
            i = f.identifiers or {}
            if any(str(i.get(k) or "").strip()
                   for k in ("user_ref", "email", "phone", "email_hash", "name")):
                return True
    return False


def locate(rid, uid):
    out = {"activity": [], "files": [], "ident": False}
    for a in ACT_BY_RES.get(rid, []):
        out["activity"].append(f"{a.activity_id}:{a.event_type}:{a.event_time}")
        if ident_in_text(a.details, uid):
            out["ident"] = True
    for fid, lines in ev_text.items():
        n = sum(1 for ln in lines if rid in ln)
        if not n:
            continue
        out["files"].append(f"{fid}({n})")
        if not out["ident"] and any(rid in ln and ident_in_text(ln, uid) for ln in lines):
            out["ident"] = True
    if not out["ident"] and fact_has_ident(rid):
        out["ident"] = True
    return out


def key(s, with_ev=True):
    base = (s["phase"], s["operation"], s["system_id"], s["resource_id"])
    return base + ((s["evidence_id"],) if with_ev else ())


def cause_of(step):
    rid = step["resource_id"]
    if rid.startswith("account_"):
        return "account resource"
    if rid.startswith("analytics_"):
        return "analytics resource"
    if rid.startswith("audit_record_"):
        return "audit_record resource"
    if rid.startswith("tombstone_"):
        return "tombstone resource"
    if rid.startswith("backup_"):
        return "backup resource"
    return f"other ({step['system_id']})"


rows = []
cause_counter = Counter()
missed_by_cause = Counter()
ev_total = Counter()
ev_miss = Counter()
ev_miss_sys = Counter()
ev_total_sys = Counter()

for cid in sorted(gold):
    gsteps = json.loads(gold[cid]["steps_json"])
    psteps = json.loads(pred[cid]["steps_json"]) if cid in pred else []
    uid = resolver.resolve(req[cid]["email"])

    pkeys = {key(s) for s in psteps}
    pbase = {key(s, False) for s in psteps}
    pos = {}
    for i, ps in enumerate(psteps):
        pos.setdefault(key(ps, False), i)

    for gs in gsteps:
        ev_total[gs["phase"]] += 1
        ev_total_sys[gs["system_id"]] += 1
        base = key(gs, False)
        if key(gs) in pkeys:
            continue
        found = base in pos
        if found:
            got_ev = psteps[pos[base]]["evidence_id"]
            ev_miss[gs["phase"]] += 1
            ev_miss_sys[gs["system_id"]] += 1
            cause = "evidence_id choice"
            loc = None
        else:
            got_ev = ""
            cause = cause_of(gs)
            missed_by_cause[cause] += 1
            loc = locate(gs["resource_id"], uid)
        cause_counter[cause] += 1
        rows.append({"case": cid, "phase": gs["phase"], "op": gs["operation"],
                     "system": gs["system_id"], "resource": gs["resource_id"],
                     "gold_ev": gs["evidence_id"], "got_ev": got_ev,
                     "predicted": "yes" if found else "NO", "cause": cause,
                     "where": loc})

    gkeys = {key(g) for g in gsteps}
    gbase = {key(g, False) for g in gsteps}
    for ps in psteps:
        if key(ps) not in gkeys and key(ps, False) not in gbase:
            cause_counter["extra step"] += 1
            rows.append({"case": cid, "phase": ps["phase"], "op": ps["operation"],
                         "system": ps["system_id"], "resource": ps["resource_id"],
                         "gold_ev": "", "got_ev": ps["evidence_id"],
                         "predicted": "EXTRA", "cause": "extra step", "where": None})

print("=" * 130)
print(f"MISS / MISMATCH TABLE (32 samples)   predicted file: {PRED}")
print("=" * 130)
print(f"{'case':<8} {'pred':<6} {'phase':<12} {'operation':<26} {'system':<20} "
      f"{'resource':<18} {'gold_ev':<11} {'got_ev':<11} cause")
for r in rows:
    print(f"{r['case']:<8} {r['predicted']:<6} {r['phase']:<12} {r['op']:<26} "
          f"{r['system']:<20} {r['resource']:<18} {r['gold_ev']:<11} {r['got_ev']:<11} {r['cause']}")

print()
print("=" * 130)
print("WHERE THE MISSING RESOURCE APPEARS IN dataset/ AND WHETHER IT CARRIES AN IDENTIFIER")
print("=" * 130)
for r in rows:
    if r["where"] is None:
        continue
    loc = r["where"]
    print(f"{r['case']} {r['resource']} [{r['cause']}]")
    print(f"   activity : {', '.join(loc['activity']) if loc['activity'] else '-'}")
    print(f"   files    : {', '.join(loc['files']) if loc['files'] else '-'}")
    print(f"   identifier co-occurrence: {'YES' if loc['ident'] else 'no'}")

print()
print("=" * 130)
print("COUNTS PER CAUSE")
print("=" * 130)
for cause, n in cause_counter.most_common():
    print(f"  {cause:<26} {n}")
print("  -- of which resource never predicted at all --")
for cause, n in missed_by_cause.most_common():
    print(f"  {cause:<26} {n}")

print()
print("evidence_id hit rate by phase:")
for ph in ("FINDING", "REMEDIATION", "VERIFICATION"):
    t = ev_total[ph]
    m = ev_miss[ph]
    print(f"  {ph:<14} {t - m}/{t} = {(t - m) / t:.1%}" if t else f"  {ph:<14} n/a")
print("evidence_id hit rate by system:")
for sysid in sorted(ev_total_sys):
    t = ev_total_sys[sysid]
    m = ev_miss_sys[sysid]
    print(f"  {sysid:<22} {t - m}/{t}")
