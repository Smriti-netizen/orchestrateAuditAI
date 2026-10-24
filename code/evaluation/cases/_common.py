"""Shared context for the participant-authored property cases under ``cases/``.

Each case module exposes ``check() -> (ok, detail)``. The context is rebuilt here
so a case can be run on its own without touching ``sample_output.csv``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from audit import loaders  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.link import Linker, absence_evidence, build_coverage  # noqa: E402


def admissible(fact, audit_at):
    """The engine's cut-off, imported rather than re-implemented.

    Manifest capture time is authoritative except for log lines, whose own
    timestamp is the observation (a later export container is not new evidence;
    the sample reference answers cite exactly those lines, and treating the
    container as the observation costs 3 positional matches).
    """
    from audit.engine import _at_or_before
    return _at_or_before(fact, audit_at)


def context():
    data = loaders.load_all()
    resolver = build_resolver(data.identities, data.audit_log)
    facts = agent.build_index()["index"].facts
    linker = Linker(resolver, facts, data.activity)
    coverage = build_coverage(facts)

    def absence_fn(system_id, resource_id, audit_at):
        return absence_evidence(coverage, system_id, resource_id, audit_at)[0]

    return {"data": data, "resolver": resolver, "facts": facts, "linker": linker,
            "coverage": coverage, "absence_fn": absence_fn}


def run(req, ctx, facts=None):
    """Analyze one request, optionally against a restricted fact set."""
    facts = ctx["facts"] if facts is None else facts
    linker = Linker(ctx["resolver"], facts, ctx["data"].activity)
    coverage = build_coverage(facts)

    def absence_fn(system_id, resource_id, audit_at):
        return absence_evidence(coverage, system_id, resource_id, audit_at)[0]

    return agent.analyze_case(req, ctx["resolver"], linker, coverage, absence_fn)
