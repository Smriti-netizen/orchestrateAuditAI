"""STEP 2: NOT_APPLICABLE and weak-classifier audit (read-only)."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from audit import config, loaders  # noqa: E402
from audit.engine import (  # noqa: E402
    DELETION_MARKERS,
    NON_DELETION_MARKERS,
    authorizes_deletion,
)

FOLLOWUP_MARKERS = (
    "profile is gone",
    "account is gone",
    "account no longer exists",
    "cannot identify me",
    "can't identify me",
    "can not identify me",
    "fully removed",
    "was removed",
    "been removed",
    "already been erased",
)

NOUNS = (
    "account",
    "profile",
    "data",
    "dados",
    "datos",
    "données",
    "konto",
    "cuenta",
    "conta",
    "アカウント",
    "계정",
)
VERBS = (
    "remov",
    "delete",
    "erase",
    "clos",
    "elimin",
    "supprim",
    "exclu",
    "削除",
    "삭제",
)


def classify_reason(text):
    t = (text or "").lower()
    for m in NON_DELETION_MARKERS:
        if m in t:
            return "NON_DELETION", m
    for m in DELETION_MARKERS:
        if m in t:
            return "DELETION_MARKER", m
    hn = [n for n in NOUNS if n in t]
    hv = [v for v in VERBS if v in t]
    if hn and hv:
        return "MULTILINGUAL_FALLBACK", f"nouns={hn} verbs={hv}"
    return "NONE", ""


def main():
    reqs = loaders.load_deletion_requests(config.DELETION_REQUESTS)
    print("=== STEP 2: NOT_APPLICABLE cases case_533..542 ===")
    for n in range(533, 543):
        cid = f"case_{n}"
        r = next(x for x in reqs if x.case_id == cid)
        group, marker = classify_reason(r.request_text)
        print(f"{cid}  fired={group!r} marker={marker!r}")
        print(f"  request_text={r.request_text}")

    print("\n=== Deletion cases via MULTILINGUAL_FALLBACK ===")
    mf = []
    for r in reqs:
        if authorizes_deletion(r.request_text):
            group, det = classify_reason(r.request_text)
            if group == "MULTILINGUAL_FALLBACK":
                mf.append((r.case_id, det, r.request_text))
    for cid, det, txt in mf:
        print(f"{cid}: {det}")
        print(f"  {txt}")
    print(f"count MULTILINGUAL_FALLBACK among authorized: {len(mf)}")

    print("\n=== Deletion cases via follow-up DELETION_MARKERS ===")
    fu = []
    for r in reqs:
        if not authorizes_deletion(r.request_text):
            continue
        t = (r.request_text or "").lower()
        hit = [m for m in FOLLOWUP_MARKERS if m in t]
        if hit:
            fu.append((r.case_id, hit, r.request_text))
    for cid, hit, txt in fu:
        print(f"{cid}: {hit}")
        print(f"  {txt}")
    print(f"count follow-up marker deletions: {len(fu)}")

    print("\n=== 518 request classifier group counts ===")
    c = Counter()
    for r in reqs:
        group, _ = classify_reason(r.request_text)
        if group == "NONE":
            group = "UNCLASSIFIED"
        if not authorizes_deletion(r.request_text):
            c[f"NOT_APPLICABLE/{group}"] += 1
        else:
            c[f"DELETION/{group}"] += 1
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
