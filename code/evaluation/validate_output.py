"""Structural validator for the submitted ``output.csv``.

Run from the repository root:

    py code/evaluation/validate_output.py [path/to/output.csv]

Checks only the published contract (AGENTS.md §6.3, §6.4): one row per request, exact
columns, sorted case ids, valid statuses, JSON steps, consecutive numbering, phase order,
the seven step fields, allowed operations/states, and the NOT_APPLICABLE rule. It does not
look at any golden answer, so it is safe to run on the hidden set.
"""
import csv
import json
import sys
from pathlib import Path

STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
OPERATIONS = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}


def main(argv):
    root = Path(__file__).resolve().parents[2]
    out = Path(argv[1]) if len(argv) > 1 else root / "output.csv"
    req_path = root / "dataset" / "deletion_requests.csv"
    errors = []

    if not out.exists():
        print(f"FAIL  {out} does not exist")
        return 1
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    wanted = [r["case_id"] for r in csv.DictReader(req_path.open(encoding="utf-8-sig"))]

    if list(rows[0].keys()) != ["case_id", "deletion_status", "steps_json"]:
        errors.append(f"columns are {list(rows[0].keys())}")
    got = [r["case_id"] for r in rows]
    if len(got) != len(wanted):
        errors.append(f"{len(got)} rows, expected {len(wanted)}")
    if len(set(got)) != len(got):
        errors.append("duplicate case ids")
    if got != sorted(got):
        errors.append("rows are not sorted by case_id")
    if set(got) != set(wanted):
        missing = sorted(set(wanted) - set(got))[:5]
        extra = sorted(set(got) - set(wanted))[:5]
        errors.append(f"case id mismatch (missing {missing}, extra {extra})")

    for row in rows:
        cid = row["case_id"]
        if row["deletion_status"] not in STATUSES:
            errors.append(f"{cid}: bad status {row['deletion_status']!r}")
        try:
            steps = json.loads(row["steps_json"])
        except json.JSONDecodeError as exc:
            errors.append(f"{cid}: steps_json is not JSON ({exc})")
            continue
        if not isinstance(steps, list):
            errors.append(f"{cid}: steps_json is not an array")
            continue
        if row["deletion_status"] == "NOT_APPLICABLE":
            if steps:
                errors.append(f"{cid}: NOT_APPLICABLE with {len(steps)} steps")
            continue
        if [s.get("step_no") for s in steps] != list(range(1, len(steps) + 1)):
            errors.append(f"{cid}: step_no not consecutive from 1")
        ranks = [PHASES.index(s.get("phase")) if s.get("phase") in PHASES else -1 for s in steps]
        if -1 in ranks or ranks != sorted(ranks):
            errors.append(f"{cid}: phase order is not FINDING -> REMEDIATION -> VERIFICATION")
        for s in steps:
            if set(s) != STEP_FIELDS:
                errors.append(f"{cid}: step {s.get('step_no')} fields are {sorted(s)}")
                continue
            op = s["operation"]
            if op not in OPERATIONS:
                errors.append(f"{cid}: step {s['step_no']} unknown operation {op}")
                continue
            phase, state = OPERATIONS[op]
            if s["phase"] != phase or s["state"] != state:
                errors.append(f"{cid}: step {s['step_no']} {op} has {s['phase']}/{s['state']}")
            if not s["system_id"] or not s["resource_id"]:
                errors.append(f"{cid}: step {s['step_no']} missing system/resource id")

    if errors:
        print(f"FAIL  {len(errors)} violation(s) in {out}")
        for e in errors[:30]:
            print("  -", e)
        return 1
    total = sum(len(json.loads(r["steps_json"])) for r in rows)
    print(f"PASS  {out}: {len(rows)} rows, {total} steps, structural contract satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
