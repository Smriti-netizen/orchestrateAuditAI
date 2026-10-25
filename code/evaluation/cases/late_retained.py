"""C6: retained records from late-exported bundles get membership but not citation.

The patch adds _late_retained() which allows retained audit records (security audit /
legal hold) from bundles exported after audit_at whose own snapshot time is at or
before audit_at. The bundle is only a container; the snapshot inside observed the
record in time. Membership is allowed, but the later export file may never be cited.

This test checks:
1. Specific cases (23, 24, 29, 30) have VERIFY_RECORD_PRESENT for their audit_record
   with evidence_id "" in INCOMPLETE plans.
2. VERIFY_RECORD_PRESENT for retained records never cites a file captured after audit_at.
3. Two synthetic fixtures pin the rule itself: a retained fact whose own snapshot time is
   at or before audit_at but whose export file is later is admitted, while a fact whose own
   snapshot time is after audit_at stays excluded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import context, run  # noqa: E402

from audit.engine import _late_retained  # noqa: E402
from audit.evidence import load_manifest  # noqa: E402
from audit.evidence.facts import Fact  # noqa: E402


def _steps_for(steps, system_id, resource_id):
    return [s for s in steps
            if s["system_id"] == system_id and s["resource_id"] == resource_id]


def _synthetic():
    """Pin the rule's two halves without touching the dataset."""
    audit_at = "2026-06-01T00:00:00Z"
    # (a) the bundle was exported after audit_at, but the snapshot inside it was taken
    #     before: the record was observed in time, so it is a member.
    in_time = Fact(file_id="synthetic_late_bundle", captured_at="2026-05-20T10:00:00Z",
                   system_id="security_service", resource_id="audit_record_9001",
                   category="SECURITY_AUDIT_LOG", presence="PRESENT",
                   extra={"file_captured_at": "2026-07-01T00:00:00Z"})
    # (b) the snapshot itself was taken after audit_at: the future is not evidence.
    future = Fact(file_id="synthetic_future", captured_at="2026-06-15T10:00:00Z",
                  system_id="security_service", resource_id="audit_record_9002",
                  category="SECURITY_AUDIT_LOG", presence="PRESENT",
                  extra={"file_captured_at": "2026-07-01T00:00:00Z"})

    admitted = _late_retained([in_time], "security_service", audit_at)
    excluded = _late_retained([future], "security_service", audit_at)
    if admitted != [in_time]:
        return False, (f"synthetic in-time snapshot in a late bundle was not admitted "
                       f"(captured_at<=audit_at, file_captured_at>audit_at): {admitted}")
    if excluded != []:
        return False, (f"synthetic snapshot taken after audit_at was admitted: {excluded}")
    return True, ("synthetic fixtures: late bundle / in-time snapshot admitted; "
                  "post-cut-off snapshot excluded")


def check():
    ok, detail = _synthetic()
    if not ok:
        return False, detail
    notes = [detail]

    ctx = context()
    manifest = {ef.file_id: ef.captured_at for ef in load_manifest()}

    # Test 1: Specific cases should have VERIFY_RECORD_PRESENT with empty evidence_id
    test_cases = {
        "case_23": "audit_record_4128",
        "case_24": "audit_record_7095",
        "case_29": "audit_record_3954",
        "case_30": "audit_record_1707",
    }

    for req in ctx["data"].requests:
        if req.case_id not in test_cases:
            continue

        rid = test_cases[req.case_id]
        status, steps = run(req, ctx)
        mine = _steps_for(steps, "security_service", rid)
        ops = [s["operation"] for s in mine]
        evidences = [s["evidence_id"] for s in mine]

        if status == "INCOMPLETE":
            has_verify = "VERIFY_RECORD_PRESENT" in ops
            has_empty_evidence = any(e == "" for e in evidences)
            if not (has_verify and has_empty_evidence):
                return False, (f"{req.case_id} {rid}: status={status} ops={ops} "
                             f"evidences={evidences} (expected VERIFY_RECORD_PRESENT with '')")
        else:
            # COMPLETE should not have the step (filtered out)
            has_verify = "VERIFY_RECORD_PRESENT" in ops
            if has_verify:
                return False, (f"{req.case_id} {rid}: COMPLETE plan has VERIFY_RECORD_PRESENT "
                             f"(should be filtered out)")

    # Test 2: VERIFY_RECORD_PRESENT steps for retained records (security/legal) should
    # have empty evidence_id if the file is captured after audit_at
    for req in ctx["data"].requests:
        status, steps = run(req, ctx)
        for s in steps:
            if (s["operation"] == "VERIFY_RECORD_PRESENT" and
                    s["system_id"] in ("security_service", "legal_service")):
                ev = s.get("evidence_id")
                if ev and ev in manifest:
                    # If evidence is cited, the file must be at or before audit_at
                    if str(manifest[ev]) > str(req.audit_at):
                        return False, (f"{req.case_id} {s['system_id']} VERIFY_RECORD_PRESENT "
                                     f"cites {ev} captured at {manifest[ev]} > audit_at {req.audit_at}")

    return True, "; ".join(notes + ["late_retained rule: all checks passed"])
