"""STEP 1 audit: which records stopped counting as "existing" after _record_exists.

Runs the generator twice in one process - once with the current parser, once with
the parser from the parent commit f9ae67d^ (where record_exists was set
unconditionally) - and diffs the statuses.  Then prints, for three of the flipped
cases, every fact the engine used for each linked resource, plus a sweep over the
whole fact index for remaining direct identifiers or replica copies.

Read-only: no prediction logic is modified.
"""
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import main as m  # noqa: E402
from audit import config, loaders  # noqa: E402
from audit.account_link import (  # noqa: E402
    add_provenance_account, add_provenance_resource, resolve_account_rows)
from audit.batch_link import resolve_analytics_rows  # noqa: E402
from audit.engine import (  # noqa: E402
    KNOWN_PREFIXES, _at_or_before, _superseded, analyze_case, decide_resource,
    authorizes_deletion)
from audit.evidence import parse_json as pj  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker, absence_evidence, build_coverage  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FLIPPED = [f"case_{n}" for n in range(383, 393)]
DETAIL = ["case_383", "case_387", "case_392"]
DIRECT = ("name", "email", "phone", "email_hash")


def old_parser_namespace():
    src = subprocess.run(
        ["git", "show", "f9ae67d^:code/audit/evidence/parse_json.py"],
        cwd=str(REPO), capture_output=True, text=True, check=True).stdout
    ns = {"__name__": "audit.evidence.parse_json", "__package__": "audit.evidence"}
    exec(compile(src, "old_parse_json.py", "exec"), ns)
    return ns


def generate(out_path):
    """Run the generator into a scratch file, never over the repo output.csv."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        return m.run_generate(out_path=str(out_path))


def old_record_exists(f):
    """record_exists exactly as the parent commit computed it."""
    extra = f.extra or {}
    if "status_code" in extra and "lookup_key" in extra:
        # service_export "profile_lookups": only a hit meant existence.
        return f.presence == "PRESENT"
    return True


def counts_as_present(f, old=False):
    if f.presence == "PRESENT":
        return True
    if old:
        return bool(old_record_exists(f))
    return bool(f.extra.get("record_exists"))


def build():
    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    built = m.build_index()
    facts = built["index"].facts
    linker = Linker(resolver, facts, data.activity)
    coverage = build_coverage(facts)
    requests = loaders.load_deletion_requests(config.DELETION_REQUESTS)
    provenance, _ = resolve_account_rows(requests, resolver, linker, data.activity)
    analytics, _ = resolve_analytics_rows(requests, resolver, linker, data.activity)
    return dict(data=data, resolver=resolver, facts=facts, linker=linker,
                coverage=coverage, requests=requests, provenance=provenance,
                analytics=analytics)


def linked_for(ctx, req):
    """Same composition run_generate feeds to analyze_case."""
    uid = ctx["resolver"].resolve(req.email)
    linked = ctx["linker"].resources(uid) if uid else {}
    prov_acc = ctx["provenance"].get(req.case_id)
    if prov_acc:
        absent_only = [f for f in ctx["linker"].facts_for("account_service", prov_acc)
                       if f.presence == "ABSENT" and not f.extra.get("record_exists")]
        linked = add_provenance_account(linked, prov_acc, absent_only)
    if req.case_id in ctx["analytics"]:
        rid = ctx["analytics"][req.case_id]
        linked = add_provenance_resource(
            linked, "analytics_platform", rid, "ANALYTICS_PROFILE",
            facts=ctx["linker"].facts_for("analytics_platform", rid))
    return uid, linked


def in_scope(req, item):
    if {"activity", "activity_provenance"} & item.link_sources:
        if item.link_times and not any(t <= req.audit_at for t in item.link_times):
            return False
        return True
    if item.facts and not any(_at_or_before(f, req.audit_at) for f in item.facts):
        return False
    return True


def absence_fn(coverage):
    return lambda s, r, a: absence_evidence(coverage, s, r, a)[0]


def main_entry():
    m.USE_LLM = False

    scratch = Path(tempfile.gettempdir()) / "record_exists_audit"
    scratch.mkdir(parents=True, exist_ok=True)
    new_rows = generate(scratch / "_flip_new.csv")
    ns = old_parser_namespace()
    real = pj.parse_json_file
    pj.parse_json_file = ns["parse_json_file"]
    try:
        old_rows = generate(scratch / "_flip_old.csv")
    finally:
        pj.parse_json_file = real

    new_map = {r["case_id"]: r for r in new_rows}
    old_map = {r["case_id"]: r for r in old_rows}
    flipped = []
    for cid in sorted(new_map):
        a, b = old_map[cid]["deletion_status"], new_map[cid]["deletion_status"]
        if a != b:
            flipped.append((cid, a, b, len(json.loads(old_map[cid]["steps_json"])),
                            len(json.loads(new_map[cid]["steps_json"]))))
    print("=== STEP 1: status change caused by the _record_exists fix ===")
    for cid, a, b, n_old, n_new in flipped:
        print(f"  {cid}: {a} -> {b}   steps {n_old} -> {n_new}")
    print(f"  total flipped: {len(flipped)}")
    print(f"  exactly case_383..case_392: {[f[0] for f in flipped] == FLIPPED}")

    ctx = build()
    afn = absence_fn(ctx["coverage"])

    print()
    print("=== per-case / per-resource detail (3 cases) ===")
    for cid in DETAIL:
        req = next(r for r in ctx["requests"] if r.case_id == cid)
        status, steps = analyze_case(
            req, ctx["resolver"], ctx["linker"], ctx["coverage"], afn,
            provenance_account=ctx["provenance"].get(req.case_id),
            provenance_analytics={ctx["analytics"][req.case_id]:
                                  ctx["linker"].facts_for(
                                      "analytics_platform",
                                      ctx["analytics"][req.case_id])}
            if req.case_id in ctx["analytics"] else None)
        print(f"\n--- {cid}  audit_at={req.audit_at}  requested_at={req.requested_at} "
              f"reported={req.reported_status}  authorizes={authorizes_deletion(req.request_text)}")
        print(f"    status={status}  steps={len(steps)}  user="
              f"{ctx['resolver'].resolve(req.email)}")
        uid, linked = linked_for(ctx, req)
        for (system_id, rid) in sorted(linked):
            if not rid.startswith(KNOWN_PREFIXES):
                continue
            item = linked[(system_id, rid)]
            if not in_scope(req, item):
                continue
            admissible = [f for f in item.facts
                          if f.system_id == system_id and _at_or_before(f, req.audit_at)]
            usable = [f for f in admissible
                      if not (f.presence == "ABSENT" and f.extra.get("event_stream")
                              and f.captured_at < req.requested_at)]
            decision = decide_resource(system_id, rid, usable,
                                       coverage=ctx["coverage"], audit_at=req.audit_at)
            old_e = any(counts_as_present(f, old=True) for f in usable)
            new_e = any(counts_as_present(f) for f in usable)
            if old_e == new_e and decision is None:
                continue
            tag = "   <== NO LONGER EXISTS" if old_e != new_e else ""
            cat = getattr(decision, "category", None) or item.category
            print(f"  * {system_id} / {rid}  category={cat}  "
                  f"old_exists={old_e} -> new_exists={new_e}{tag}")
            if decision is not None:
                print(f"      decision: finding={decision.finding!r} "
                      f"remediation={decision.remediation!r} "
                      f"verification={decision.verification}/{decision.verify_state} "
                      f"finding_evidence={decision.finding_evidence!r} "
                      f"verify_evidence={decision.verify_evidence!r}")
            else:
                print("      decision: None (resource dropped out of the plan)")
            for f in usable:
                ids = f.identifiers or {}
                non_empty = [k for k in DIRECT if str(ids.get(k) or "").strip()]
                print(f"      fact {f.file_id} cap={f.captured_at} "
                      f"file_cap={(f.extra or {}).get('file_captured_at')} "
                      f"presence={f.presence} topo={f.topology} "
                      f"anon={bool(f.extra.get('anonymized'))} "
                      f"redact={bool(f.extra.get('redacted'))} "
                      f"record_exists={bool(f.extra.get('record_exists'))} "
                      f"counts_as_present={counts_as_present(f)}")
                print(f"          ids_non_empty={non_empty or ['(none)']} "
                      f"user_ref={(ids.get('user_ref') or '')!r} "
                      f"admissible={_at_or_before(f, req.audit_at)} "
                      f"superseded={_superseded(f, rid, ctx['coverage'], req.audit_at) if _at_or_before(f, req.audit_at) else False} "
                      f"collection={(f.extra or {}).get('collection')}")

    print()
    print("=== global sweep over the whole fact index (all files, all topologies) ===")
    seen = set()
    for cid in DETAIL:
        req = next(r for r in ctx["requests"] if r.case_id == cid)
        uid, linked = linked_for(ctx, req)
        for (system_id, rid) in sorted(linked):
            if (system_id, rid) in seen:
                continue
            seen.add((system_id, rid))
            allf = ctx["linker"].facts_for(system_id, rid)
            ident_rows, present_rows, replica_rows = [], [], []
            for f in allf:
                ids = f.identifiers or {}
                direct = {k: str(ids[k]) for k in DIRECT
                          if str(ids.get(k) or "").strip()}
                if direct:
                    ident_rows.append((f.file_id, f.captured_at, f.presence,
                                       f.topology, sorted(direct)))
                if counts_as_present(f):
                    present_rows.append(f)
                    if f.topology not in ("primary", "n/a"):
                        replica_rows.append(f)
            if not ident_rows and not present_rows:
                continue
            bad_ident = any(r[2] == "PRESENT" for r in ident_rows)
            flag = ""
            if bad_ident:
                flag += "  <== DIRECT IDENTIFIER ON A PRESENT ROW"
            if replica_rows:
                flag += "  <== PRESENT IN A REPLICA"
            print(f"  {cid} {system_id}/{rid}: facts={len(allf)} "
                  f"identifying_rows={len(ident_rows)} "
                  f"present_rows={len(present_rows)} "
                  f"replica_present={len(replica_rows)}{flag}")
            for r in ident_rows:
                print(f"      ident {r[0]} cap={r[1]} presence={r[2]} topo={r[3]} "
                      f"fields={r[4]}")
            for f in replica_rows:
                print(f"      replica {f.file_id} cap={f.captured_at} "
                      f"presence={f.presence} topo={f.topology}")


if __name__ == "__main__":
    main_entry()
