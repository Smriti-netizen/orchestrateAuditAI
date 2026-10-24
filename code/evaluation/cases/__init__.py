"""Participant-authored evaluation cases (property tests, run by custom_cases.py)."""
from . import post_audit_snapshot, retained_and_flow  # noqa: F401

EXTRA_CASES = [
    ("C4 post-audit evidence ignored", post_audit_snapshot.check),
    ("C5 retained record + data-flow recreation", retained_and_flow.check),
]
