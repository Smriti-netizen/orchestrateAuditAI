"""Ghost Data Still Exists - deletion-audit agent (M1 milestone: loaders, manifest, parsers, fact index).

Run: python3 code/main.py [--cases dataset/deletion_requests.csv] [--out output.csv] [--no-llm]
"""
import argparse
from collections import Counter
from pathlib import Path

from audit import config, loaders, media
from audit.engine import analyze_case, classify_disagreements
from audit.identity import build_resolver
from audit.link import Linker, absence_evidence, build_coverage, idents_match, normalize_system
from audit.evidence import FactIndex, load_manifest
from audit.evidence import parse_json as _json
from audit.evidence import parse_log as _log
from audit.evidence import parse_sql as _sql
from audit.evidence import parse_text as _text
from audit.evidence.facts import SYSTEM_CATEGORY as _SYSTEM_CATEGORY
from audit.evidence.facts import category_for as _category_for

USE_LLM = True


def _facts_for(file_id: str, path: Path, captured_at: str):
    """Dispatch by extension -> (facts, extra, errors)."""
    suffix = path.suffix.lower()
    if suffix == ".sql":
        facts, errors = _sql.parse_sql_file(str(path), file_id, captured_at)
        return facts, None, errors
    if suffix == ".json":
        facts, errors = _json.parse_json_file(str(path), file_id, captured_at)
        return facts, None, errors
    if suffix == ".log":
        facts, events, errors = _log.parse_log_file(str(path), file_id, captured_at)
        return facts, {"events": events}, errors
    if suffix == ".txt":
        facts, errors = _text.parse_text_file(str(path), file_id, captured_at)
        return facts, None, errors
    if suffix == ".pdf":
        try:
            text = media.pdf_text(path)
        except Exception as exc:
            return [], None, [f"{file_id}: PDF text extraction failed: {exc}"]
        facts = media.pdf_to_facts(text, file_id, captured_at)
        return facts, None, [] if facts else [f"{file_id}: PDF has no normalisable records"]
    if suffix == ".png":
        doc = media.cached_image_doc(file_id, path)
        if doc is None:
            if not USE_LLM:
                return [], None, [f"{file_id}: image not extracted and --no-llm"]
            try:
                from audit.llm import LLMClient, LLMError, load_catalog, pick_vision_model
                catalog = load_catalog()
                model = pick_vision_model(catalog)
                extracted = media.extract_image(LLMClient(model=model), model, path)
            except Exception as exc:
                return [], None, [f"{file_id}: vision extraction failed: {exc}"]
            media.save_image_doc(file_id, path, captured_at, extracted)
            doc = {"extracted": extracted}
        return media.doc_to_facts(doc["extracted"], file_id, captured_at), None, []
    if suffix == ".csv":
        return [], None, [f"{file_id}: csv evidence not supported"]
    return [], None, [f"{file_id}: unknown extension {path.suffix}"]


def build_index(evidence_dir=None, manifest_path=None) -> dict:
    """Parse every manifest file once into the normalised fact index."""
    manifest = load_manifest(manifest_path)
    index = FactIndex()
    per_file, failed, events = [], [], []
    for ef in manifest:
        p = Path(evidence_dir) / ef.path if evidence_dir else config.DATA_DIR / ef.path
        facts, extra, errors = _facts_for(ef.file_id, p, ef.captured_at)
        facts = facts if facts is not None else []
        per_file.append((ef.file_id, ef.path, len(facts), errors))
        for f in facts:
            # Manifest capture time is the authority for the audit_at cutoff:
            # a file captured after audit_at is inadmissible even if it embeds
            # earlier per-row timestamps.
            f.extra["file_captured_at"] = ef.captured_at
            f.system_id = normalize_system(f.system_id)
            # One authoritative category pass: parsers differ (JSON/SQL/log/text),
            # so any UNKNOWN is re-derived from the system, collection and
            # resource-id prefix in one place.
            # Only systems the static map cannot name are re-derived here.  A
            # parser that deliberately left a fact UNKNOWN for a *known* system
            # did so on purpose (e.g. a contact-cache lookup), and overriding
            # that would change the policy branch for the resource.
            if (not f.category or f.category == "UNKNOWN") \
                    and f.system_id not in _SYSTEM_CATEGORY:
                f.category = _category_for(f.system_id,
                                           f.extra.get("collection"),
                                           f.resource_id, f.extra)
            index.add(f)
        if isinstance(extra, dict) and extra.get("events"):
            events.extend(extra["events"])
        if errors:
            failed.append((ef.file_id, ef.path, errors))
    return {"index": index, "manifest": manifest, "failed": failed,
            "per_file": per_file, "events": events}


def run_m1(evidence_dir=None, manifest_path=None) -> dict:
    built = build_index(evidence_dir, manifest_path)
    manifest = built["manifest"]
    index = built["index"]
    per_file = built["per_file"]
    failed_files = built["failed"]

    print("=" * 70)
    print("M1 SUMMARY: evidence parse")
    print(f"manifest files: {len(manifest)}")
    print(f"files parsed with facts: {len(per_file) - len(failed_files)}")
    print(f"files with parse errors/deferred: {len(failed_files)}")
    print("-" * 70)
    print(f"{'file_id':<12} {'path':<55} {'facts':>6}  errors")
    for fid, p, n, errs in sorted(per_file, key=lambda x: x[0]):
        status = "FAIL" if errs else "ok"
        print(f"{fid:<12} {p:<55} {n:>6}  {status}  {errs}")
    print("-" * 70)
    print("FAILED / DEFERRED FILES:")
    for fid, p, n, errs in failed_files:
        print(f"  {fid} {p}: {errs}")
    print("=" * 70)

    # facts per system
    by_sys = {}
    for f in index.facts:
        by_sys.setdefault(f.system_id, {"facts": 0, "resources": set()})
        by_sys[f.system_id]["facts"] += 1
        by_sys[f.system_id]["resources"].add(f.resource_id)
    print("FACTS PER SYSTEM:")
    for sid in sorted(by_sys):
        d = by_sys[sid]
        print(f"  {sid:<24} facts={d['facts']:<5} resources={len(d['resources'])}")

    # resource ids per system (top)
    print("RESOURCE IDS PER SYSTEM (sample):")
    for sid in sorted(by_sys):
        ids = sorted(by_sys[sid]["resources"])[:12]
        print(f"  {sid}: {ids}")

    # dataset-level quick loads
    data = loaders.load_all()
    print("=" * 70)
    print(f"requests={len(data.requests)} identities={len(data.identities)} "
          f"audit_log={len(data.audit_log)} activity={len(data.activity)} rules={len(data.rules)}")
    return {"index": index, "manifest": manifest, "failed": failed_files, "per_file": per_file}


def run_m2(sample_path=None) -> dict:
    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    samples = loaders.load_deletion_requests(sample_path or config.SAMPLE_REQUESTS)

    print("=" * 70)
    print("M2 SUMMARY: identity resolution")
    print(f"identities={len(data.identities)} audit_log={len(data.audit_log)}")

    resolved, unmapped = {}, []
    for r in samples:
        uid = resolver.resolve(r.email)
        resolved[r.case_id] = uid
        if uid is None:
            unmapped.append((r.case_id, r.email))
    ok = len(samples) - len(unmapped)
    print(f"sample cases resolved: {ok} / {len(samples)}")
    if unmapped:
        print(f"UNRESOLVED: {unmapped}")
        raise SystemExit("M2 FAIL: unresolvable sample emails")

    # Distinct user_ids: the 32 samples must not collapse onto decoy accounts.
    uids = sorted({u for u in resolved.values() if u})
    print(f"distinct user_ids: {len(uids)}")

    print("-" * 70)
    print(f"{'case':<8} {'email':<34} {'user_id':<10} ids(e/p/h/n)")
    for r in samples:
        uid = resolved[r.case_id]
        ids = resolver.user_identifier_set(uid)
        print(f"{r.case_id:<8} {r.email:<34} {uid or '-':<10} "
              f"{len(ids['emails'])}/{len(ids['phones'])}/{len(ids['hashes'])}/{len(ids['names'])}")

    # Cross-check: do the resolved identifiers actually appear in the evidence?
    built = build_index()
    facts = built["index"].facts
    events = built["events"]
    print("-" * 70)
    print(f"{'case':<8} {'uid':<10} {'facts':>5} {'events':>6} {'files':>5}  systems")
    per_case_facts = {}
    for r in samples:
        uid = resolved[r.case_id]
        ids = resolver.user_identifier_set(uid)
        matched = [f for f in facts if _idents_match(f.identifiers, ids)]
        matched_ev = [e for e in events if _idents_match(e.get("identifiers") or {}, ids)]
        per_case_facts[r.case_id] = matched
        files = sorted({f.file_id for f in matched} | {e["file_id"] for e in matched_ev})
        systems = sorted({f.system_id for f in matched} | {e["system_id"] for e in matched_ev})
        print(f"{r.case_id:<8} {uid:<10} {len(matched):>5} {len(matched_ev):>6} {len(files):>5}  "
              f"{','.join(systems)[:60]}")

    # Also confirm every evaluation request resolves (no silent dropouts).
    all_reqs = data.requests
    dr = [r for r in all_reqs if resolver.resolve(r.email) is None]
    print("-" * 70)
    print(f"evaluation requests: {len(all_reqs)}  unresolved: {len(dr)}")
    for r in dr[:10]:
        print(f"  UNRESOLVED {r.case_id} {r.email}")
    return {"resolver": resolver, "resolved": resolved, "unresolved_all": dr,
            "per_case_facts": per_case_facts}


def _idents_match(identifiers, ids):
    """Exact-only match of a fact/event identifier dict to a user's identifier set."""
    identifiers = identifiers or {}
    ref = str(identifiers.get("user_ref") or "").strip()
    if ref and ref == ids["user_id"]:
        return True
    email = str(identifiers.get("email") or "").strip().lower()
    if email and email in ids["emails"]:
        return True
    h = str(identifiers.get("email_hash") or "").strip().lower()
    if h and h in ids["hashes"]:
        return True
    phone = "".join(ch for ch in str(identifiers.get("phone") or "") if ch.isdigit())
    if phone and phone in ids["phones"]:
        return True
    return False


def run_link_check(sample_path=None) -> dict:
    """M4 report: linked resources per sample case + absence-citation calibration."""
    import csv
    import json

    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    built = build_index()
    facts = built["index"].facts
    linker = Linker(resolver, facts, data.activity)
    coverage = build_coverage(facts)
    samples = loaders.load_deletion_requests(sample_path or config.SAMPLE_REQUESTS)

    expected = {}
    with open(config.SAMPLE_OUTPUT, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            expected[row["case_id"]] = json.loads(row["steps_json"])

    print("=" * 78)
    print("M4: identifier-only resource linking")
    print(f"facts={len(facts)} coverage_records={len(coverage)}")

    missing = []
    print("-" * 78)
    for req in samples:
        uid = resolver.resolve(req.email)
        linked = linker.resources(uid)
        by_system = {}
        for (system_id, resource_id) in sorted(linked):
            by_system.setdefault(system_id, []).append(resource_id)
        summary = "; ".join(f"{s}:{','.join(v)}" for s, v in sorted(by_system.items()))
        print(f"{req.case_id} {uid}: {summary or 'NO LINKED RESOURCES'}")

        expected_ids = {(s["system_id"], s["resource_id"])
                        for s in expected[req.case_id]}
        for system_id, resource_id in sorted(expected_ids):
            if (system_id, resource_id) not in linked:
                missing.append((req.case_id, uid, system_id, resource_id))

    print("-" * 78)
    print(f"expected resources not linked: {len(missing)}")
    for case_id, uid, system_id, resource_id in missing:
        print(f"  MISSING {case_id} {uid} {system_id} {resource_id}")

    # Absence citation calibration: latest covering snapshot at/before audit_at.
    print("-" * 78)
    print("absence-citation calibration (COMPLETE cases, VERIFY_RECORD_ABSENT):")
    total = hit = 0
    for req in samples:
        uid = resolver.resolve(req.email)
        linked = linker.resources(uid)
        for step in expected[req.case_id]:
            if step["operation"] != "VERIFY_RECORD_ABSENT":
                continue
            key = (step["system_id"], step["resource_id"])
            item = linked.get(key)
            present_any = bool(item and item.facts)
            chosen, candidates = absence_evidence(
                coverage, step["system_id"], step["resource_id"], req.audit_at,
                resource_present_anywhere=False)
            total += 1
            ok = chosen == step["evidence_id"]
            hit += ok
            if not ok:
                print(f"  {req.case_id} {key[1]}: expected {step['evidence_id']} got {chosen} "
                      f"candidates={[c[0] for c in candidates[:3]]} facts={present_any}")
    print(f"  citation hit rate: {hit}/{total}")
    return {"linker": linker, "coverage": coverage, "missing": missing,
            "hit": hit, "total": total}


def run_generate(cases_path=None, out_path=None):
    """Produce output.csv for every request (the graded artefact)."""
    import csv
    import json

    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    built = build_index()
    facts = built["index"].facts
    linker = Linker(resolver, facts, data.activity)
    coverage = build_coverage(facts)

    def absence_fn(system_id, resource_id, audit_at):
        return absence_evidence(coverage, system_id, resource_id, audit_at)[0]

    requests = loaders.load_deletion_requests(cases_path or config.DELETION_REQUESTS)
    from audit.account_link import resolve_account_rows
    from audit.batch_link import resolve_analytics_rows
    provenance, provenance_ambiguous = resolve_account_rows(
        requests, resolver, linker, data.activity)
    analytics, analytics_ambiguous = resolve_analytics_rows(
        requests, resolver, linker, data.activity)
    analytics_facts = {rid: linker.facts_for("analytics_platform", rid)
                       for rid in analytics.values()}
    rows, unresolved, disagreements = [], [], []
    for req in requests:
        status, steps = analyze_case(
            req, resolver, linker, coverage, absence_fn,
            provenance_account=provenance.get(req.case_id),
            provenance_analytics={analytics[req.case_id]: analytics_facts[analytics[req.case_id]]}
            if req.case_id in analytics else None)
        uid = resolver.resolve(req.email)
        if uid is None:
            unresolved.append(req.case_id)
        # M5: recorded request classifications are claims, never the deciding
        # factor - we only surface where the intake claim and our own reading
        # of request_text disagree.
        for d in classify_disagreements(req, uid,
                                        linker.resources(uid) if uid else {},
                                        data.activity):
            disagreements.append(dict(d, case_id=req.case_id))
        rows.append({"case_id": req.case_id, "deletion_status": status,
                     "steps_json": json.dumps(steps, ensure_ascii=False,
                                              separators=(",", ":"))})
    rows.sort(key=lambda r: r["case_id"])
    target = out_path or config.OUTPUT_PATH
    with open(target, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["case_id", "deletion_status", "steps_json"])
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter
    counts = Counter(r["deletion_status"] for r in rows)
    print(f"wrote {len(rows)} rows -> {target}")
    print("statuses:", dict(sorted(counts.items())))
    print(f"unresolved subjects: {len(unresolved)}")
    print(f"provenance-linked accounts: {len(provenance)} "
          f"(ambiguous/omitted: {len(provenance_ambiguous)})")
    print(f"batch-linked analytics: {len(analytics)} "
          f"(ambiguous/omitted: {len(analytics_ambiguous)})")
    print(f"CLASSIFY_REQUEST disagreements: {len(disagreements)}")
    for d in disagreements:
        print(f"  {d['case_id']} {d['activity_id']} {d['resource_id']}: "
              f"intake={d['claim']} classifier={d['classifier']}")
    return rows


def run_eval(out_path=None):
    """Score a generated file against the sample golden answers (calibration only)."""
    import csv
    import json

    def load(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return {r["case_id"]: r for r in csv.DictReader(fh)}

    gold = load(config.SAMPLE_OUTPUT)
    got = load(out_path or config.OUTPUT_PATH)
    status_ok = steps_ok = 0
    total_steps = matched_steps = 0
    field_match = 0
    seq_ok = 0
    for case_id, g in gold.items():
        gsteps = json.loads(g["steps_json"])
        r = got.get(case_id)
        if not r:
            continue
        ssteps = json.loads(r["steps_json"])
        status_ok += r["deletion_status"] == g["deletion_status"]
        ok = (len(gsteps) == len(ssteps)
              and all(a["operation"] == b["operation"]
                      and a["system_id"] == b["system_id"]
                      and a["resource_id"] == b["resource_id"]
                      and a["state"] == b["state"]
                      and a["evidence_id"] == b["evidence_id"]
                      for a, b in zip(gsteps, ssteps)))
        steps_ok += ok
        if [x["operation"] for x in gsteps] == [x["operation"] for x in ssteps]:
            seq_ok += 1
        total_steps += len(gsteps)
        for a, b in zip(gsteps, ssteps):
            same = (a["phase"] == b["phase"] and a["operation"] == b["operation"]
                    and a["system_id"] == b["system_id"]
                    and a["resource_id"] == b["resource_id"]
                    and a["state"] == b["state"] and a["evidence_id"] == b["evidence_id"])
            matched_steps += same
            field_match += sum(a[k] == b[k] for k in
                               ("phase", "system_id", "resource_id", "operation",
                                "state", "evidence_id"))
    n = len(gold)
    print(f"cases={n}")
    print(f"status accuracy: {status_ok}/{n} = {status_ok / n:.1%}")
    print(f"exact-step-set accuracy: {steps_ok}/{n} = {steps_ok / n:.1%}")
    print(f"operation-sequence accuracy: {seq_ok}/{n} = {seq_ok / n:.1%}")
    print(f"step match (positional): {matched_steps}/{total_steps} = "
          f"{matched_steps / max(total_steps, 1):.1%}")
    print(f"field match (positional): {field_match}/{total_steps * 6} = "
          f"{field_match / max(total_steps * 6, 1):.1%}")
    return {"status": status_ok, "n": n}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ghost Data Still Exists deletion-audit agent")
    ap.add_argument("--cases", default=str(config.DELETION_REQUESTS))
    ap.add_argument("--out", default=str(config.OUTPUT_PATH))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--m1", action="store_true", help="evidence parse summary")
    ap.add_argument("--m2", action="store_true", help="run identity resolution check on sample requests")
    ap.add_argument("--sample", default=None, help="sample request CSV for --m2/--link")
    ap.add_argument("--link", action="store_true", help="M4 resource-linking report")
    ap.add_argument("--eval", action="store_true", help="score --out against sample_output.csv")
    args = ap.parse_args(argv)
    global USE_LLM
    USE_LLM = not args.no_llm
    if args.eval:
        run_eval(args.out)
        print("EVAL DONE")
        return 0
    if args.link:
        run_link_check(args.sample)
        print("M4 LINK DONE")
        return 0
    if args.m2:
        summary = run_m2(args.sample)
        print("M2 DONE")
        return 0
    if args.m1:
        summary = run_m1()
        print("M1 DONE")
        return 0
    run_generate(args.cases, args.out)
    print("GENERATE DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
