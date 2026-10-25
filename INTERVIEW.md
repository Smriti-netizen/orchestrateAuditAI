# Ghost Data Still Exists — interview prep (one page)

## Architecture (8 lines)

1. Load typed CSVs and the evidence manifest; parse every evidence file into normalized **Facts** (system, resource, category, presence, identifiers, topology, timestamps).
2. Build an exact **identity resolver** (current row + audit-log history) and an identifier-only **linker** (no names, no proximity).
3. Optional **provenance** links: ranked one-to-one account resolver (`account_link.py`) and structurally forced analytics batches (`batch_link.py`).
4. **Coverage** maps which snapshots enumerate whole collections for absence proofs.
5. For each request: **authorize** from `request_text`, then **analyze** only resources with admissible facts or in-window provenance/activity links at `audit_at`.
6. **Policy** maps category → DELETE / ANONYMIZE / REDACT / RETAIN; replicas compared independently.
7. **Plan** FINDING → REMEDIATION → VERIFICATION with stable ordering; findings cite evidence, future work uses `""`.
8. Emit `output.csv` in numeric `case_id` order; PNG evidence uses cached VLM JSON under `code/media_extractions/` (0 LLM calls on graded run).

## Ten key decisions (with measured numbers)

1. **Exact identifier linking only** — 0 colliding emails/phones/hashes; 0 ambiguous resolves on 518+32 emails.
2. **Account provenance resolver** — 342/518 cases get a distinct account row (0 reused); 0.25 LOO floor; 176 omitted.
3. **Provenance attaches only ABSENT account facts** — guesses may prove absence, never a remaining-record finding; 0 status flips from wrong-account 404 reuse.
4. **Analytics batch rule (structural k≥2 only)** — 2/2 sample pairs; 54/518 batch links; 132 ambiguous batches skipped.
5. **`_record_exists` = PRESENT ∨ anonymized ∨ redacted** — rejected literal PRESENT-only (18/32 exact, 158/193 positional); same 518 status counts (216/292/10), 12 cases differ only in step content.
6. **Post-`audit_at` membership leak fix** — 158 pairs removed; −22 steps; samples stay 21/32 exact, 174/193 positional. The one exception: a retained audit/legal record (`SECURITY_AUDIT_LOG` / `LEGAL_HOLD_RECORD`) whose own snapshot time is before `audit_at` is a member even when its export file is later — member, never cited (`evidence_id ""`).
7. **`_superseded()` guard** — 0 pairs on 518 (1113 late snapshots all after audit_at); kept for synthetic C4 fixture.
8. **Replica drift = finding** — present in any topology counts as present; not collapsed away.
9. **LLM only for 11 PNGs** — 33 cache-building calls, ≈$0.119 total; **0** calls on final `--no-llm` 518 run.
10. **32-sample calibration gate** — 32/32 status, 21/32 exact, 25/32 sequence, 174/193 positional, 1079/1158 field before shipping full run. Full 518 run: 216/292/10, 2629 steps.

## Six rejected ideas (with numbers)

1. **Batch-order / anchor account mapping** — precision 0.25–1.00 on 2–11 picks.
2. **`record_exists = (presence == PRESENT)` only** — 18/32 exact, 158/193 positional regression.
3. **Per-stream “newest row” evidence selection** — broke case_09 citation (evidence_93 → evidence_06); reverted.
4. **CHECK_RESTORE_ACCESS claims override evidence** — 4/123 linked resources contradict facts.
5. **Stricter post-audit membership (file-time logs / identifier-only links)** — −3 or −9 positional matches on samples.
6. **Broad UNKNOWN→category fallback** — broke case_10 support_002 (3 positional steps); narrowed to legal/export prefixes only.

## Five honest limitations

1. **13 missing sample steps** on public calibration: ~6 account, ~5 analytics, ~2 audit_record (documented in evaluation_report.md).
2. **Coin-flip analytics batches** at k=2 — expected precision 0.5 when order is unobservable; omission scores 0.
3. **Only 32 labelled sample cases** — full 518 graded hidden; metrics are structural + property tests + sample fit.
4. **Post-`audit_at` evidence is inadmissible** — can miss resources first seen after cut-off (158 pairs dropped); case_231 still asymmetric.
5. **Media extraction depends on a cached VLM run** — reproducible offline only if `code/media_extractions/` ships with the zip.

## Eight likely judge questions (3-line answers)

**Why guess accounts at all?**  
The worker’s account delete row has no identifier, so identifier linking never attaches it. The ranked resolver is the smallest measured exception (342/518, 0 reuse); omitting below 0.25 LOO is honest about uncertainty.

**Why use workflow claims for provenance?**  
Claims are not proof of deletion state — only a timestamped hint for *which* account row the worker touched. Candidates must match claim time windows and exclude rows owned by other subjects.

**How are decoys avoided?**  
Linking never uses names; only exact email/phone/hash/user_ref on the same row or explicit link events. Three shared-name users produced 0 false links in measurement.

**How is replica drift handled?**  
Each topology is evaluated separately; PRESENT in any replica is a remaining record or drift finding. Snapshots are not merged into a single “logical deleted” view.

**How is ordering guaranteed?**  
Steps are built in FINDING → REMEDIATION → VERIFICATION phases, then sorted by operation rank, `system_id`, and `resource_id`; output rows use numeric `case_id` order.

**Why LLM only for images?**  
PDFs and structured exports parse deterministically; only 11 PNGs needed vision. Extractions are sha256-cached under `code/media_extractions/` so graded runs make 0 API calls.

**How are hidden cases protected from overfitting?**  
Development used the 32 public samples plus five property tests (authorization, historical email, multilingual text, post-audit cut-off, retention/flow) — no hidden labels, no golden 518 in repo.

**What would you improve next?**  
Higher-recall analytics linking without breaking the k≥2 safety proof; richer multilingual authorization without widening English substring false positives; optional human-review queue for 176 omitted account provenance cases.
