"""Participant-authored evaluation cases (independent of sample_output.csv).

These are *property* tests, not golden-step copies: each case asserts what the
contract requires of the agent, so they still hold if the surrounding sample
answers are never consulted. Run:

    python3 code/evaluation/custom_cases.py        (Windows: py ...)

The three cases target the parts of the contract that are easiest to get wrong:
  1. authorization gate  - a non-deletion request must never produce steps;
  2. identifier breadth  - a historical email must resolve to the same subject
                           and produce identical steps as the current email;
  3. multilingual scope  - a deletion request written in another language must
                           be treated as an authorized deletion.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

import csv  # noqa: E402

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.engine import analyze_case, authorizes_deletion  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker, absence_evidence, build_coverage  # noqa: E402


def _ctx():
    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    built = agent.build_index()
    facts = built["index"].facts
    linker = Linker(resolver, facts, data.activity)
    coverage = build_coverage(facts)

    def absence_fn(system_id, resource_id, audit_at):
        return absence_evidence(coverage, system_id, resource_id, audit_at)[0]

    return data, resolver, linker, coverage, absence_fn


def _run(req, resolver, linker, coverage, absence_fn):
    return analyze_case(req, resolver, linker, coverage, absence_fn)


def _subject_with_records(data, resolver, linker, coverage, absence_fn,
                          template=None, text=None):
    """An email whose subject yields real audit steps for the given window.

    Filtering on linked resources alone is not enough: the records may all fall
    outside the request's audit window, which would make a negative test look
    like a pass. So the candidate must actually produce steps.
    """
    template = template or data.requests[0]
    text = text or "Please close my account and delete all personal data."
    for ident in data.identities:
        uid = resolver.resolve(ident.email)
        if not uid or not linker.resources(uid):
            continue
        req = type(template)(**{**template.__dict__,
                                "email": ident.email, "request_text": text})
        _status, steps = analyze_case(req, resolver, linker, coverage, absence_fn)
        if steps:
            return ident.email
    raise RuntimeError("no subject produces audit steps for this window")


def case_1_authorization_gate(data, resolver, linker, coverage, absence_fn):
    """A deletion-flavoured case with an access-only message must be N/A.

    The same subject with real deletion wording is audited first, so this proves
    the authorization gate suppressed genuine steps rather than finding nothing.
    """
    base = data.requests[0]
    subject = _subject_with_records(data, resolver, linker, coverage, absence_fn,
                                    base)
    deletion_text = "Please close my account and delete all personal data."
    delete_req = type(base)(**{**base.__dict__,
                               "request_text": deletion_text, "email": subject})
    _, delete_steps = _run(delete_req, resolver, linker, coverage, absence_fn)
    text = ("Please send me a copy of the personal information you hold about me. "
            "I am only asking for a copy of the information; do not change anything.")
    req = type(base)(**{**base.__dict__,
                        "request_text": text, "email": subject})
    status, steps = _run(req, resolver, linker, coverage, absence_fn)
    ok = status == "NOT_APPLICABLE" and steps == [] and len(delete_steps) > 0
    return ok, (f"subject={subject} deletion-control steps={len(delete_steps)}; "
                f"access-request status={status} steps={len(steps)} "
                f"(expected NOT_APPLICABLE/0)")


def case_2_historical_email(data, resolver, linker, coverage, absence_fn):
    """A request sent from a superseded email must audit the same subject."""
    change = next(r for r in data.audit_log if r.field_name == "email")
    old_email = change.old_value
    current_email = next(i.email for i in data.identities
                         if i.user_id == change.user_id)
    # Reuse a real deletion request so the audit window is realistic, but send
    # it from the historical address and the current address respectively.
    template = data.requests[0].__dict__
    text = "Please delete my account and all personal data associated with it."
    old_req = type(data.requests[0])(**{**template, "email": old_email,
                                       "request_text": text})
    new_req = type(data.requests[0])(**{**template, "email": current_email,
                                        "request_text": text})
    old_status, old_steps = _run(old_req, resolver, linker, coverage, absence_fn)
    new_status, new_steps = _run(new_req, resolver, linker, coverage, absence_fn)
    same_subject = resolver.resolve(old_email) == resolver.resolve(current_email)
    ok = same_subject and old_status == new_status and old_steps == new_steps
    return ok, (f"old={old_email} -> {resolver.resolve(old_email)} "
                f"current={current_email} -> {resolver.resolve(current_email)} "
                f"status {old_status}/{new_status} steps "
                f"{len(old_steps)}/{len(new_steps)}")


def case_3_multilingual(data, resolver, linker, coverage, absence_fn):
    """A Portuguese deletion request is an authorized deletion."""
    text = ("Gostaria de excluir a minha conta e todos os dados pessoais "
            "associados. Por favor apaguem tudo.")
    base = data.requests[0]
    subject = _subject_with_records(data, resolver, linker, coverage, absence_fn,
                                    base, text)
    req = type(base)(**{**base.__dict__,
                        "request_text": text, "email": subject})
    status, steps = _run(req, resolver, linker, coverage, absence_fn)
    ok = (authorizes_deletion(text) and status in ("COMPLETE", "INCOMPLETE")
          and len(steps) > 0)
    return ok, (f"authorized={authorizes_deletion(text)} status={status} "
                f"steps={len(steps)}")


CASES = [
    ("C1 authorization gate (non-deletion text)", case_1_authorization_gate),
    ("C2 historical-email resolution", case_2_historical_email),
    ("C3 multilingual deletion request", case_3_multilingual),
]


def run():
    ctx = _ctx()
    print("=" * 78)
    print("Custom participant-authored evaluation cases")
    print("=" * 78)
    passed = 0
    for name, fn in CASES:
        try:
            ok, detail = fn(*ctx)
        except Exception as exc:  # a raised case is a failed case
            ok, detail = False, f"EXCEPTION {exc!r}"
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print(f"      {detail}")
    print("-" * 78)
    print(f"custom cases passed: {passed}/{len(CASES)}")
    return passed == len(CASES)


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
