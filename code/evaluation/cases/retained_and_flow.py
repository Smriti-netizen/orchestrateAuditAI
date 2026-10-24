"""C5: retained records stay present, and an enabled flow really is a recreation risk.

Two contract rules are checked on real subjects:

* a ``SECURITY_AUDIT_LOG`` record that policy requires to be retained must be
  reported as ``VERIFY_RECORD_PRESENT`` - never as a remaining record to delete;
* a ``data_flow_*`` connector whose admissible evidence says ``enabled`` must produce
  the finding + remediation + verification triple, and the *same* subject replayed
  with the flag flipped to ``False`` must produce only the verification step. That
  proves the state comes from evidence rather than from a workflow result.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import admissible, context, run  # noqa: E402


def _steps_for(steps, system_id, resource_id):
    return [s for s in steps
            if s["system_id"] == system_id and s["resource_id"] == resource_id]


def _find(ctx, system_id, prefix, predicate):
    for req in ctx["data"].requests:
        uid = ctx["resolver"].resolve(req.email)
        if not uid:
            continue
        for (sid, rid), item in sorted(ctx["linker"].resources(uid).items()):
            if sid != system_id or not rid.startswith(prefix):
                continue
            hits = [f for f in item.facts
                    if f.system_id == sid and admissible(f, req.audit_at)
                    and predicate(f)]
            if hits:
                return req, rid
    return None, None


def check():
    ctx = context()

    # (a) retained audit record -> VERIFY_RECORD_PRESENT, never DELETE_RECORD
    req, rid = _find(ctx, "security_service", "audit_record_",
                     lambda f: f.presence == "PRESENT")
    if req is None:
        return False, "no retained security audit record is linked to any request"
    _status, steps = run(req, ctx)
    mine = _steps_for(steps, "security_service", rid)
    ops = [s["operation"] for s in mine]
    wrong = [op for op in ops
             if op in ("DELETE_RECORD", "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII")]
    retained_ok = ops == ["VERIFY_RECORD_PRESENT"] and not wrong

    # (b) enabled data flow -> finding + remediation + verification; flipped -> clean
    req2, rid2 = _find(ctx, "integration_service", "data_flow_",
                       lambda f: f.extra.get("enabled") is True)
    if req2 is None:
        return False, "no enabled data flow is linked to any request"
    _status2, steps_on = run(req2, ctx)
    on_ops = [s["operation"] for s in _steps_for(steps_on, "integration_service", rid2)]

    flipped = []
    for f in ctx["facts"]:
        if (f.system_id == "integration_service" and f.resource_id == rid2
                and f.extra.get("enabled") is not None):
            clone = copy.copy(f)
            clone.extra = dict(f.extra)
            clone.extra["enabled"] = False
            flipped.append(clone)
        else:
            flipped.append(f)
    _status3, steps_off = run(req2, ctx, facts=flipped)
    off_ops = [s["operation"] for s in _steps_for(steps_off, "integration_service", rid2)]

    flow_ok = ("REPORT_ACTIVE_DATA_FLOW" in on_ops and "DISABLE_DATA_FLOW" in on_ops
               and "VERIFY_DATA_FLOW_DISABLED" in on_ops
               and "REPORT_ACTIVE_DATA_FLOW" not in off_ops
               and "VERIFY_DATA_FLOW_DISABLED" in off_ops)

    ok = retained_ok and flow_ok
    return ok, (f"retained {rid} on {req.case_id}: {ops} (expected "
                f"[VERIFY_RECORD_PRESENT]); flow {rid2} on {req2.case_id}: "
                f"enabled={on_ops} flipped={off_ops}")
