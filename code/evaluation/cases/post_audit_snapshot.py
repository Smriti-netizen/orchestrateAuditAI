"""C4: evidence captured after ``audit_at`` must not change the answer.

Three contract properties (§6.2/§6.6) are checked on real requests that genuinely
carry linked facts captured after their cut-off:

1. **citation** - a step may only cite a file that genuinely supports it at the cut-off:
   either the file holds an admissible fact for that record, or it is a covering snapshot
   captured at or before ``audit_at``. (A log line keeps its own timestamp, so a later
   bulk-export container is fine - see evaluation_report.md entry 7.)
2. **discovery** - a resource that is only discoverable from post-cut-off evidence or
   a post-cut-off activity link may not appear in any step;
3. **status** - the status must be identical when the index is rebuilt from the
   cut-off facts alone (a future snapshot may add or remove a provable step, but it
   may never turn an incomplete deletion into a complete one, or the reverse).

Property 3 compares status only: a resource discovered by a later snapshot can give
us a provably-absent record we would not otherwise know about, which is how the
reference answers treat account rows (see evaluation_report.md, C4).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import admissible, context, run  # noqa: E402

from audit.evidence import load_manifest  # noqa: E402


def check():
    ctx = context()
    manifest = {ef.file_id: ef.captured_at for ef in load_manifest()}
    by_file_resource = {}
    for f in ctx["facts"]:
        by_file_resource.setdefault((f.file_id, f.system_id, f.resource_id), []).append(f)
    cited_late, future_steps, checked = [], [], 0
    status_changed, step_diff = [], []

    for req in ctx["data"].requests:
        uid = ctx["resolver"].resolve(req.email)
        if not uid:
            continue
        later = [f for item in ctx["linker"].resources(uid).values()
                 for f in item.facts if not admissible(f, req.audit_at)]
        if not later:
            continue
        status, steps = run(req, ctx)
        cutoff = [f for f in ctx["facts"] if admissible(f, req.audit_at)]
        cut_status, cut_steps = run(req, ctx, facts=cutoff)
        checked += 1

        for s in steps:
            ev = s.get("evidence_id")
            if not ev:
                continue
            support = by_file_resource.get((ev, s["system_id"], s["resource_id"]), [])
            if support:
                # A cited fact must itself be admissible. The file may be a later
                # bulk export container (log lines carry their own timestamp).
                if not any(admissible(f, req.audit_at) for f in support):
                    cited_late.append((req.case_id, ev, s["resource_id"], "fact"))
            elif manifest.get(ev, "9999") > req.audit_at:
                # No fact in that file: the citation is a covering snapshot, which
                # must be a point-in-time capture at or before the cut-off.
                cited_late.append((req.case_id, ev, s["resource_id"], "snapshot"))
            item = ctx["linker"].resources(uid).get((s["system_id"], s["resource_id"]))
            if item and item.facts and not any(admissible(f, req.audit_at)
                                               for f in item.facts):
                future_steps.append((req.case_id, s["resource_id"]))
        if status != cut_status:
            status_changed.append((req.case_id, status, cut_status))
        if steps != cut_steps:
            step_diff.append(req.case_id)
        if checked >= 60:
            break

    ok = checked >= 20 and not cited_late and not future_steps and not status_changed
    return ok, (f"{checked} cases with post-cut-off linked facts; late citations="
                f"{len(cited_late)} future-only steps={len(future_steps)} "
                f"status changes={len(status_changed)}; step-set differences when "
                f"post-cut-off evidence is removed entirely={len(step_diff)}"
                f"{' (' + ','.join(step_diff[:3]) + ')' if step_diff else ''}")
