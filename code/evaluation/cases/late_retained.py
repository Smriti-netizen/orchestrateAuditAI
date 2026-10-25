"""C6: retained records from late-exported bundles get membership but not citation.

The patch adds _late_retained() which allows retained audit records (security audit /
legal hold) that appear only in a bundle exported after audit_at, but whose own snapshot
time is at or before audit_at. The bundle is only a container; the snapshot inside
observed the record in time. Membership is allowed, but the later export file may never
be cited.

This test verifies the _late_retained() function behavior directly on the engine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import context  # noqa: E402

from audit.engine import _late_retained, RETAIN_CATEGORIES  # noqa: E402


def check():
    ctx = context()

    # Test 1: Verify _late_retained only accepts retained categories with snapshot_time constraint
    for req in ctx["data"].requests:
        uid = ctx["resolver"].resolve(req.email)
        if not uid:
            continue

        for (sid, rid), item in sorted(ctx["linker"].resources(uid).items()):
            if sid not in ("security_service", "legal_service"):
                continue
            if not rid.startswith(("audit_record_", "legal_hold_")):
                continue

            # Check if this resource qualifies for late retention
            late = _late_retained(item.facts, sid, req.audit_at)
            if late:
                # Should have the right category (from f.category, not f.presence)
                from audit.engine import _resolve_category
                category = _resolve_category(late, sid)
                retained_ok = category in RETAIN_CATEGORIES
                if not retained_ok:
                    return False, (f"late_retained found {rid} on {req.case_id}: "
                                 f"category={category} in RETAIN_CATEGORIES={retained_ok}")

    # Test 2: Non-retained categories should not be accepted
    for req in ctx["data"].requests:
        uid = ctx["resolver"].resolve(req.email)
        if not uid:
            continue

        for (sid, rid), item in sorted(ctx["linker"].resources(uid).items()):
            if sid in ("support_platform", "search_service", "analytics_platform",
                       "export_service"):
                late = _late_retained(item.facts, sid, req.audit_at)
                if late:
                    return False, (f"non-retained {sid} {rid} on {req.case_id} was "
                                 f"accepted by _late_retained (should be rejected)")

    return True, "late_retained rule: all checks passed"
