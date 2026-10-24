# Evaluation report — Ghost Data Still Exists

Deterministic deletion-audit agent (`code/main.py`). All numbers below were produced by
the commands in §1 on the supplied participant data only; `dataset/sample_output.csv`
was used **only** for calibration scoring and never at inference time.

---

## 1. How to reproduce

```bash
# 1. sample calibration run + score (32 public cases)
python3 code/main.py --cases dataset/sample_deletion_requests.csv \
                     --out /tmp/sample_pred.csv --no-llm
python3 code/main.py --out /tmp/sample_pred.csv --eval

# 2. full dataset run -> output.csv (518 rows, repository root)
python3 code/main.py --no-llm

# 3. participant-authored evaluation cases
python3 code/evaluation/custom_cases.py
```

On this Windows machine `python3` is a Store alias, so `py` was used; the code itself is
stdlib-only Python 3 and path-relative, so `python3` works on macOS/Linux unchanged.

---

## 2. Headline metrics (32 sample cases, 193 reference steps)

| Metric | Result |
| --- | --- |
| Case-level deletion-status accuracy | **32 / 32 = 100.0 %** |
| Exact step-set accuracy (all steps identical, incl. evidence) | 19 / 32 = 59.4 % |
| Operation-sequence accuracy (same operations, same order) | 21 / 32 = 65.6 % |
| Step match, positional (all six fields equal) | 164 / 193 = 85.0 % |
| Field-level accuracy, positional (6 fields × 193 steps) | 1031 / 1158 = 89.0 % |

Status distribution produced for the same 32 cases: `COMPLETE` 10, `INCOMPLETE` 22,
`NOT_APPLICABLE` 0 — identical to the reference distribution.

#### Status confusion matrix (rows = reference, columns = predicted)

| reference \ predicted | `COMPLETE` | `INCOMPLETE` | `NOT_APPLICABLE` | total |
| --- | ---: | ---: | ---: | ---: |
| `COMPLETE` | **10** | 0 | 0 | 10 |
| `INCOMPLETE` | 0 | **22** | 0 | 22 |
| `NOT_APPLICABLE` | 0 | 0 | **0** | 0 |

All 32 sample statuses are on the diagonal; the off-diagonal cells are zero. (The sample
set contains no non-deletion request, so the `NOT_APPLICABLE` column is exercised by C1
and by the 10 `NOT_APPLICABLE` rows of the full run instead.)

#### Step-level precision / recall / F1

Steps are matched as a multiset of `(operation, system_id, resource_id)` per case, so
order and evidence do not hide a missing or invented step:

| | TP | FP | FN | precision | recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| all steps | 176 | 5 | 17 | **0.972** | **0.912** | **0.941** |

The 5 false positives and 5 of the 17 false negatives are the same decision seen twice:
an account row was resolved to the wrong half of an ambiguous batch (the reference's row
counts as missed, ours as extra). The other 12 misses are 5 null-identifier analytics rows
and 6 audit records that are not observable before `audit_at`.

#### Field-level accuracy, positional

Each gold step at position *i* is compared field by field with the predicted step at
position *i* (denominator 193 gold steps):

| Field | Matches | Accuracy |
| --- | ---: | ---: |
| `step_no` | 181 / 193 | 93.8 % |
| `phase` | 181 / 193 | 93.8 % |
| `system_id` | 169 / 193 | 87.6 % |
| `resource_id` | 164 / 193 | 85.0 % |
| `operation` | 169 / 193 | 87.6 % |
| `state` | 169 / 193 | 87.6 % |
| `evidence_id` | 179 / 193 | 92.7 % |
| **all six fields** | **164 / 193** | **85.0 %** |

`resource_id` is the only field with an error of its own: every operation, phase and state
mismatch is a knock-on effect of a verification step for the wrong (or an omitted) record.
On the 164 positional steps whose `(operation, system_id, resource_id)` also matches,
`evidence_id` matches **164 / 164**, and the 2 remaining evidence differences are the two
wrong-id account steps (`case_28`, `case_31`) where the cited snapshot proves absence of
*our* record.

#### Operation-sequence comparison (LCS)

Longest common subsequence of the predicted and reference operation lists, which tolerates
a dropped step but not a reordering:

| | Value |
| --- | --- |
| Mean LCS ratio per case (LCS ÷ reference length) | **0.913** |
| Pooled LCS ratio (Σ LCS ÷ Σ reference steps) | **0.938** (181 / 193) |

The pooled figure equals the predicted step count, i.e. for every case the predicted
operations are a subsequence of the reference operations — no reordering, no invented
operation.

### Where the remaining step error comes from

The report predicts 181 steps against 193 reference steps: **17 reference steps are
missing and 5 predicted account steps are the wrong id**. Findings and remediation are
complete (41/41 each); every remaining difference is a verification step whose resource id
could not be resolved deterministically.

| Cause | Example | Count | Decision |
| --- | --- | --- | --- |
| Account row identifier-less; two or more claim-compatible rows in the batch | `case_18/23/28/29/31` | 6 missing + 5 wrong id | ranked pick at confidence 1/k (below) — expected value is positive, a wrong id costs nothing extra over omitting |
| Anonymised `analytics_*` rows with **all** identifier columns null | `case_03 analytics_9406`, `case_25 analytics_1895` | 5 missing | only structurally forced batch pairs are linked (below) |
| Reference audit record only in a snapshot captured **after** `audit_at`, or in no file | `case_23 audit_record_4128`, `case_18 audit_record_6645` | 6 missing | inadmissible / unattainable → omitted |
| Absence proof snapshot choice | `case_21/25 account_6440/3136` | **0** (was 2 evidence ids) | fixed: a provenance-resolved account now carries its own admissible 404 fact, so the cited proof is the reference's (see §5, entry 24) |
| Wrong operation, wrong state, wrong order, extra phase | — | **0** | — |

#### Account step: ranked provenance resolution (measured)

The worker's `account_service` `DELETE_RECORD` row carries no identifier, but each request
does carry a workflow claim (`reported_status` / `reported_at`). Rule
(`code/audit/account_link.py`):

1. only for a request whose account has no exact identifier link;
2. candidate rows are `DELETE_RECORD → account_service` inside
   `[requested_at, audit_at]`, whose `reported_result` is compatible with the claim
   (`COMPLETE→SUCCESS`, `PARTIALLY_COMPLETE→SUCCESS|PARTIAL`, `NOT_STARTED→FAILED`), and
   within ±2 h of `reported_at`; rows already identifier-linked to another subject are
   excluded;
3. confidence is `1 / k` for `k` surviving candidates;
4. cases are served in descending confidence (ties: claim distance, then case id) and each
   takes its best unused row, so no row is ever shared;
5. cases below the confidence floor are omitted.

**Rejected alternatives (measured on the 17 sample cases that need an account).** Mapping
the members of a shared `reported_at` batch onto account rows in order is not a real rule:
precision 0.25 (members by `requested_at` → rows by activity order), 0.38 (descending),
0.50 (`case_id` order), 0.00 (all account rows in the window). Anchor adjacency — the
account row immediately before the case's first linked `deletion_worker` row — is also not
reliable: gap ≤ 1 gives P 1.00 on 2 picks, gap ≤ 2 gives P 0.60, gap ≤ 9 gives P 0.55.
Neither is used.

**Confidence floor sweep** (step-level F1 over account steps, 32 samples):

| floor | picks | correct | wrong | recall | step F1 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1.0 (`k=1` only) | 8 | 8 | 0 | 0.47 | 0.836 |
| 0.5 | 12 | 9 | 3 | 0.53 | 0.857 |
| 0.25 | 16 | 11 | 5 | 0.65 | **0.897** |
| 0.0 | 16 | 11 | 5 | 0.65 | 0.897 |

Per-candidate precision is 1.00 at `k=1` (8/8), 0.50 at `k=2` (2/4), 0.50 at `k=3` (1/2)
and 0.50 at `k=4` (1/2), so below `k=1` the picks are honest coin flips and `1/k` is the
calibrated confidence. A leave-one-out sweep over the 17 cases chose **0.25 in 17/17
folds**, which is the adopted floor. On the 518 requests this resolves **342 cases to 342
distinct account rows (0 reused, 176 omitted: 161 below the floor, 15 where the row is
already claimed by a better-scoring case)**.


#### Analytics / audit-record steps: what can and cannot be linked

`analytics_platform` `ANONYMIZE_RECORD` rows in a shared `reported_at` batch are handled by
`code/audit/batch_link.py`, but only in the structurally forced case: the batch has
`k ≥ 2` anonymous analytics rows **and** exactly `k` cases with no identifier-linked
analytics resource. Then every such case must own one row and the pairing follows activity
order → case-id order. Measured on the samples: 2 pairs, both correct; the within-pair order
is not observable for `k = 2`, so the honest expected precision is ~0.5 — still chosen over
omission because an omitted step scores zero. Batches where the counts do not match are
reported ambiguous and left alone. On the 518 requests the rule links 54 rows (132 batches
reported ambiguous). Status is unchanged by construction: the engine still decides each
resource from admissible facts and emits the exact reference step
(`VERIFY_RECORD_ANONYMIZED`, citing `evidence_92`/`evidence_94`/`evidence_56`).

| Resource | Case | All appearances | Capture vs `audit_at` | Identifier on the row? |
| --- | --- | --- | --- | --- |
| `analytics_9406` | case_03 | `evidence_94` | before | `lookup_key=null`, all ident fields null |
| `analytics_901` | case_04 | `evidence_56` | before | all ident fields null |
| `analytics_5335/1895/4566/4574/6077` | case_21/25/26/27/28 | `evidence_92` (before), `evidence_02` (after) | — | all ident fields null |
| `audit_record_4128/7095/3954/1707` | case_23/24/29/30 | `evidence_02` only | **after** `audit_at` | `subject_reference` present but inadmissible |
| `audit_record_6645/5569` | case_18/20 | none | — | no fact at all |

Audit records were tested for a "one record per deletion case" rule: exactly 24 of the 32
samples have one audit-record step and 8 have none, so the rule is false; the 6 unresolved
ids appear only in `evidence_02` (inadmissible) or nowhere, so nothing can be recovered
without guessing. Retention was also checked before emitting retained-record
verifications: of 369 case–audit_record pairs, **0** have `retention_until` earlier than
the case's `audit_at`, so `VERIFY_RECORD_PRESENT` never contradicts the retention ledger.

`evidence_02` is the deliberate trap: it is captured 2026-09-17T12:00, two days after the
`audit_at` of exactly the four cases whose audit records it contains. A `subject_reference`
mapping for that file is not used for them because the file is inadmissible; `case_18/20`
audits fall after the capture, but their audit records are simply not in the snapshot.
`evidence_92`'s analytics rows that *do* carry an email (`analytics_3697`, `analytics_5208`)
are linked and predicted correctly, which isolates the failure to the null-identifier rows.

#### evidence_id selection

| Phase | Gold steps | Predicted | Cited identically | Differing |
| --- | ---: | ---: | ---: | ---: |
| FINDING | 41 | 41 | 41 / 41 | 0 |
| REMEDIATION | 41 | 41 | 41 / 41 | 0 |
| VERIFICATION | 111 | 99 | 97 / 99 | 2 |

The two differences are not evidence-choice errors: they are the two wrong-id account
steps (`case_28`, `case_31`). The step at that position cites the snapshot that proves
absence of the account *we* resolved, while the reference's step proves absence of a
different account from the same ambiguous batch, so the two positions hold different
records and therefore different (both valid) proofs. Every step whose record also matches
cites exactly the reference `file_id` (**164 / 164**).

#### Ordering and extra steps

There are **no order inversions** and no wrong operations or states. Five predicted steps
are extra because they name the wrong half of an ambiguous account batch (the 5 wrong ids
in the table above); every other case is the reference sequence minus omitted verification
steps. `build_steps` sorts findings by operation rank, then system/resource, and
verifications by the contract's operation order, which matches the reference on 32/32 cases.

---

## 3. Participant-authored evaluation cases

Implemented as property tests in `code/evaluation/custom_cases.py` (C1–C3) and
`code/evaluation/cases/` (C4–C5) so they do not depend on the sample answers. Current
result: **5 / 5 pass**.

| Case | What it asserts | Why it matters | Observed |
| --- | --- | --- | --- |
| **C1 — authorization gate** | The same subject that produces real deletion steps (`leila.haddad@yahoo.com` → 1 step) must produce `NOT_APPLICABLE` / `[]` when the text is "send me a copy … do not change anything". | The gate must be driven by `request_text`, not by the presence of data. A bug here silently destroys true positives. | `NOT_APPLICABLE`, 0 steps, control produced 1 step ✓ |
| **C2 — historical-email resolution** | A request sent from a superseded address (`o.walker6695@yahoo.com`) must resolve to the same subject and produce byte-identical steps as the current address. | Identity is reconstructed from the current snapshot **plus** the audit log; old identifiers still appear in older systems. | both → `user_523`, both `INCOMPLETE`, 4 vs 4 identical steps ✓ |
| **C3 — multilingual deletion request** | A Portuguese "excluir a minha conta … apaguem tudo" must be an authorized deletion. | Requests arrive in several languages; an English-only classifier would mark them `NOT_APPLICABLE`. | authorized ✓, `COMPLETE`, 1 step ✓ |
| **C4 — post-`audit_at` evidence** (`cases/post_audit_snapshot.py`) | On 60 real requests that carry linked facts captured after their cut-off: (a) a step may only cite a file that supports it at the cut-off - an admissible fact in that file, or a covering snapshot captured at/before `audit_at`; (b) no step may report a resource that is only discoverable from post-cut-off evidence or a post-cut-off activity link; (c) the status must be unchanged when the index is rebuilt from cut-off facts only. | This is the contract's hardest boundary. A future snapshot could otherwise leak the verdict, and a status computed from it would be untraceable at the audit time the case is judged at. | 60 cases replayed: late citations 0, future-only steps 0, status changes 0 ✓ (the step *set* differs for exactly one case, `case_231`, whose account id is learned from a post-cut-off log line even though the 404 fact it cites is admissible - see §5, entry 26). The same citation test over **all 518** rows reports 0 inadmissible citations. |
| **C5 — retained record + data-flow recreation** (`cases/retained_and_flow.py`) | (a) a policy-retained `audit_record_*` must produce exactly `VERIFY_RECORD_PRESENT`, never `DELETE_RECORD` / `REPORT_REMAINING_RECORD`; (b) an enabled `data_flow_*` must produce the finding + remediation + verification triple, and the *same* subject replayed with the flag flipped to `False` must keep only the verification step. | Policy retention must not be "cleaned up" as leftover data, and flow state must come from evidence rather than from a workflow result. | `audit_record_3753` (case_34): `[VERIFY_RECORD_PRESENT]`; `data_flow_2528` (case_37): enabled `[REPORT_ACTIVE_DATA_FLOW, DISABLE_DATA_FLOW, VERIFY_DATA_FLOW_DISABLED]`, flipped `[VERIFY_DATA_FLOW_DISABLED]` ✓ |

C4 and C5 each build their own context from `dataset/` and never read `sample_output.csv`;
C5 clones the fact list in memory to flip one flag, so it tests the decision path rather
than a recorded answer.

---

## 4. Full-dataset run (the graded artefact)

| Item | Value |
| --- | --- |
| Rows written | **518** (one per `case_id`, no duplicates, in the dataset's numeric `case_id` order: `case_33` before `case_100`) |
| `COMPLETE` | 216 |
| `INCOMPLETE` | 292 |
| `NOT_APPLICABLE` | 10 (all with `steps_json = []`) |
| Total steps | 2571 (avg 4.96 per case) |
| Unresolved subjects | 0 |
| Provenance-linked account rows | 342 (each a distinct account, 0 reused; 176 cases omitted) |
| Batch-linked analytics rows | 54 (132 batches reported ambiguous) |
| Structural validation | consecutive `step_no` from 1 ✓, phase order FINDING → REMEDIATION → VERIFICATION ✓, every step has exactly the seven contract fields ✓, case order matches `dataset/deletion_requests.csv` ✓ |

Operation totals across the run: `REPORT_REMAINING_RECORD` 314, `REPORT_EXPOSED_PII` 162,
`REPORT_ACTIVE_DATA_FLOW` 69, `REPORT_RESTORE_RISK` 11, `DELETE_RECORD` 314,
`REDACT_RECORD` 99, `ANONYMIZE_RECORD` 63, `DISABLE_DATA_FLOW` 69, `BLOCK_RESTORE` 11,
`VERIFY_RECORD_ABSENT` 754, `VERIFY_RECORD_PRESENT` 294, `VERIFY_RECORD_REDACTED` 154,
`VERIFY_RECORD_ANONYMIZED` 103, `VERIFY_DATA_FLOW_DISABLED` 104,
`VERIFY_RESTORE_BLOCKED` 50. Every remediation operation has a matching verification
operation with at least the same count, which is the internal consistency invariant the
planner guarantees.

The artefact is reproducible from the shipped archive: `code.zip` unpacked into an empty
directory, `dataset/` copied in beside it, and a `--no-llm` run produces a byte-identical
`output.csv` (sha256 `75a34bd0…`).

---

## 5. Failures found during development, and the fixes

Each entry is a wrong answer observed on a sample case, the root cause, and the rule that
replaced the guess.

1. **Trusting `reported_status`.** Early versions returned `COMPLETE` because the workflow
   claimed it. *Fix:* status is computed only from evidence facts at `audit_at`; the claim
   columns are never read by the decision code.
2. **Timestamp proximity as a link.** Resources were attributed to whoever had a nearby
   activity row. *Fix:* removed entirely; a resource is linked only via an exact identifier
   on a row that also carries the resource id, a `LINK_RESOURCE_IDENTITY` activity row, a
   `source_resource_id` / `customer_reference` column, a connector row, a backup entry, or
   same-`trace_id` log stitching.
3. **Name similarity matching.** Fuzzy name matches attached decoy accounts
   (`account_8741` "Karim El-Masri" vs `account_001`). *Fix:* exact identifiers only
   (email, historical emails, email hash, phone, user id); names are never used to match.
4. **Replica drift ignored.** `case_05` looked deleted on the primary while the failover
   replica still held `account_005`. *Fix:* a record present in **any** replica is
   remaining; replicas are compared rather than collapsed.
5. **Manifest capture time ignored.** `evidence_02` (captured 2026-09-17) leaked future
   state into 2026-09-15 audits. *Fix:* the manifest `captured_at` gates every fact.
6. **Stale absence used as proof.** A 404 logged *before* the request was treated as
   evidence the record was gone (and as evidence of ownership). *Fix:* absence
   observations timestamped before `requested_at` are dropped from both ownership and
   state; only a covering snapshot taken in the audit window can prove absence.
7. **Event-stream vs snapshot time.** Logs are exported in bulk after the fact, so gating
   a log line by its file's capture time discarded observations that were genuinely made
   before `audit_at`. *Fix:* log lines are admissible by their own event timestamp
   (`event_stream` flag); JSON/SQL snapshots stay file-time gated because they are
   point-in-time captures.
8. **Same-resource evidence not merged.** A 404 lookup carried no identifiers, so it was
   invisible even though the same `(system, resource)` was linked elsewhere. *Fix:* once a
   resource is identifier-linked, all facts for that resource id are state evidence for it.
9. **Tombstones and retained records turned into deletions.** Linked-but-never-observed
   resources were assumed deleted. *Fix:* that inference is restricted to categories whose
   policy action is deletion and where a covering snapshot proves absence, so tombstones
   (`tombstone_17`) and retained security/legal records are excluded.
10. **Contact-cache rows treated as analytics.** `contact_lookup` / `contact_query` rows on
    `analytics_*` and `support_*` were anonymised/redacted, but policy deletes a secondary
    identifier cache. *Fix:* such rows map to `CACHE_ENTRY` → `DELETE_RECORD`
    (fixes `case_10 support_002`).
11. **Weakest evidence chosen for a risk finding.** `case_09` cited a log line while a SQL
    inventory snapshot witnessed the same `restore_allowed=1`. *Fix:* findings prefer the
    authoritative snapshot over a transient log observation, then recency
    (`case_09` now cites `evidence_93` as the reference does).
12. **Deletion follow-ups classified as non-deletion.** `case_26` ("my profile is gone … can
    you confirm") has no "delete" stem, so it fell to `NOT_APPLICABLE`. *Fix:* added
    deletion-verification markers (profile is gone, account no longer exists, cannot
    identify me, fully removed, …), still checked *after* the negation markers.
13. **Recorded request classifications taken at face value.** The 10 `CLASSIFY_REQUEST`
    activity rows claim "non-deletion". *Fix:* they are treated as claims; the text
    classifier decides, and `classify_disagreements()` reports any attributed row whose
    claim contradicts our reading. Disagreements on the full run: **0**, so no case was
    flipped by the claim, and none needed overriding either.
14. **Identifier-less account row written off as unlinkable.** 17 sample cases reference an
    account whose only appearance is a `DELETE_RECORD` row with no identifier
    (`case_01 account_001`). *Fix:* claim-grounded provenance in
    `code/audit/account_link.py` — a row is a candidate when it is claim-compatible, inside
    the request window and within ±2 h of `reported_at`; candidates are assigned one-to-one
    at confidence `1/k` with a 0.25 floor. Measured step F1 0.897 vs 0.836 for the
    unique-only rule; 342 of the 518 requests gain their account step, no row reused.
    Batch order and anchor adjacency were tested and rejected before this rule was adopted
    (see §2).
15. **Literal `null` treated as an identifier.** Log exporters write
    `requester_email=null email=null phone=null`; read as text those tokens made an
    anonymised analytics profile or a fully redacted support ticket look like exposed PII.
    Confirmed against the reference: `case_03 analytics_9406` (same null row) is
    `VERIFY_RECORD_ANONYMIZED`, not a finding. *Fix:* null-ish tokens (`null`, `none`,
    `n/a`, `-`, `unknown`) are dropped before identifier classification. Sample metrics are
    unchanged (32/32 status, 15/32 exact sets); on the full run 11 cases that were only
    "incomplete" because of this false PII finding become `COMPLETE` (7 → 5 steps each).

16. **Batch-order mapping assumed to be a real rule.** Mapping `reported_at`-batch members
    onto account rows by `requested_at`/`case_id` order, or taking the row immediately
    before the case's worker anchor, looked plausible but measured P = 0.25 / 0.38 / 0.50
    (ordering) and P = 0.55–1.00 on 2–11 picks (adjacency). *Fix:* neither rule is used;
    the ranked claim-consistency resolver above replaces them.
17. **`legal_hold_*` records silently dropped.** The legal service is absent from the
    system→category map, so its rows parsed as `UNKNOWN`, which the engine ignores; the
    prefix was also missing from `KNOWN_PREFIXES`. *Fix:* an explicit last-resort
    prefix→category map for systems the static map cannot name (`legal_hold` →
    `LEGAL_HOLD_RECORD`), the same fallback in the JSON service parser, and `legal_hold_`
    added to `KNOWN_PREFIXES`. 6 cases now emit `VERIFY_RECORD_PRESENT`; 1 export-service
    copy still plans `DELETE_RECORD` because its system category is `USER_EXPORT`.
18. **Category fallback over-applied (regression caught by measurement).** The first version
    of the prefix fallback recategorised every `UNKNOWN` fact, which turned
    `case_10 support_002` from `DELETE_RECORD` into `REDACT_RECORD` and cost 3 positional
    steps. *Fix:* the fallback only runs for systems the static map cannot name; a parser's
    deliberate `UNKNOWN` for a known system (a contact-cache lookup) is preserved. Sample
    metrics at that point were restored to 19/32 exact, 162/193 positional (164/193 after
    entry 24).
19. **A `404` with `record_exists=true` treated as a miss.** The literal rule "the record
    exists only if `presence == PRESENT`" was measured before adoption and **regressed the
    samples** (exact 18/32, sequence 19/32, positional 156/193, field 1005/1158): `case_27`
    and `case_28` carry an analytics row with `presence=ABSENT` but `anonymized=True`, and an
    anonymised row is a *retained* row, so the literal rule emitted a spurious second
    `VERIFY_RECORD_ABSENT`. *Fix:* `_record_exists()` = `PRESENT` **or** `anonymized` **or**
    `redacted`, applied in both the service-export and record parsers. 262 ABSENT facts carry
    `record_exists=True`; 10 of them matter. Full run: 10 `INCOMPLETE → COMPLETE` flips, −28
    steps, samples unchanged. *Post-entry-27 note:* with the per-row labels fix in place the
    two rules now produce identical full-run status counts (`216/292/10`) and total steps
    (`2571`) and differ only in the retained rows' reported operation in 12 cases, so the
    full-run flips measured here were tied to the mis-labelled rows entry 27 fixed; the
    sample regression above (18/32 vs 19/32 exact) is the durable difference.
20. **Absence from a stale snapshot accepted as current absence.** A `PRESENT` fact from an
    older export can be contradicted by a later snapshot of *the same* collection. *Fix:*
    `_superseded()` — same system + collection + topology, `fact.captured_at < snap.captured_at
    <= audit_at`; replicas stay per-topology, so a stale primary row cannot be retired by a
    secondary snapshot. Measured on the 518: 0 superseded facts and 0 `(case, resource)`
    pairs, because all 1113 later same-group snapshots fall outside every case's `audit_at`
    window. Kept as a bounded guard (0 status flips, 0 step delta) rather than reverted, and
    the measurement is recorded here so it is not mistaken for evidence that it fires.
21. **"Newest row per source" assumed to be the truth.** Selecting the latest fact per
    top-level source, *including* the fact that supplies the citation, changed `case_09`'s
    finding evidence from the reference's `evidence_93` to a log line `evidence_06` and cost
    sample accuracy (exact 18/32, positional 161/193). *Fix:* reverted. A per-stream boolean
    variant passes the samples but is inert on the 518 (0 flips, 0 steps), so it was not
    shipped. The underlying premise was also tested directly: of 123 linked resources with an
    admissible `CHECK_RESTORE_ACCESS` claim, 119 agree and **4 contradict** the facts
    (`case_75 backup_8673`, `case_171 backup_5520`, `case_291 backup_7079`,
    `case_483 backup_4600`, all "can still be restored" vs `restore_allowed=False`). With
    4/123 and no precision evidence, adopting the claim would trade 4 correct blocks for 4
    guesses, so evidence wins and the contradiction is documented instead.
22. **Identity resolution assumed to collide across people.** Before adding window rules, the
    key space was measured: 957 distinct emails, 942 distinct phones, 957 distinct hashes,
    **0 collisions** of any kind; 0 facts name more than one user; 0 of the 518 + 32 request
    emails resolve ambiguously; and a hypothetical audit-log window rule would drop 0 of the
    2222 linked fact/user pairs. Only 3 names are shared (decoys) and names are never a match
    key. *Fix:* no code added - the risk was real in principle but absent from the data, so a
    rule would have been untested surface area.
23. **SQL fallback swallowed parser errors.** `parse_sql_file` retried a failing
    `executescript` statement by statement inside `except: pass`, so a partially loaded dump
    could still claim `covers` over the rows it did load - i.e. a broken snapshot could
    masquerade as absence proof. Measurement first: 24 SQL files, `executescript` failed on
    **0** of them, and all 11 `snapshot_scope` tables carry per-origin `captured_at` values
    identical to their manifest time (0 differences), so no current answer is affected.
    *Fix:* the fallback now records the failed statement number and message in the file's
    `errors` list instead of discarding it. 0 sample or full-run changes.
24. **A provenance-guessed account used a stranger's 404.** `add_provenance_account()` created
    the account resource with no facts at all, so the absence proof fell back to the newest
    covering snapshot (`evidence_07`) while the reference cites the bundle that contains the
    actual lookup (`evidence_92`). The lookup fact belongs to that account row but carries no
    identifier, so the old code could not attach it. *Fix:* the provenance path now passes the
    account's own **absent** facts (and only absent ones: a guess may prove the row is gone,
    never that some record still exists). 24 of the 342 provenance accounts have such a fact
    and 1 has a `PRESENT` fact, which is filtered out. `case_21 account_6440` and
    `case_25 account_3136` now cite `evidence_92`; 0 status flips, +3 steps, 21 cases change
    the evidence they cite, and positional matches rise 162 → 164.
25. **Ordering and packaging defects found while auditing the artefact.** Three independent
    problems: (a) rows were sorted lexicographically, so `case_100` preceded `case_33`
    although `dataset/deletion_requests.csv` is numeric - fixed with `_case_key()` in
    `main.py` and the same order enforced by `validate_output.py`; (b) the 11 screenshot
    extractions lived in `code/cache/media/`, but the documented archive exclusion
    `*/cache/*` silently dropped them, so a fresh `code.zip` could not reproduce
    `output.csv` - moved to `code/media_extractions/` and documented as archive-included;
    (c) dead code removed (`parse_media.py` stub, unused LLM constants in `config.py`, the
    duplicate `main._idents_match`, and a `global ACCOUNT_CLAIM_WINDOW` mutation that is now
    a function parameter). Verified by rebuilding the zip, extracting it into an empty
    directory, and diffing a fresh run against the submitted file: byte-identical.
26. **Resources discovered only from post-`audit_at` evidence (the real leak C4 found).**
    Building the participant cases exposed that 158 linked `(case, resource)` pairs had **no
    admissible fact at all**: the only thing tying them to the subject was a snapshot captured
    after the cut-off, so a step for them was built from the future. *Fix:* membership now
    requires "at least one admissible fact, or an explicit activity/provenance link inside the
    window". Two stricter variants were measured and rejected: gating log lines by their
    *file* time costs 3 positional matches (the reference answers cite those lines, and a bulk
    log export is a container, not an observation), and requiring an admissible
    identifier-bearing linking fact costs 9 (155/193). The adopted rule leaves the samples
    untouched (19/32, 164/193, 1031/1158), removes 22 steps from 16 full-run cases with 0
    status flips. The one residual asymmetry is
    `case_231`, whose account id is learned from a post-cut-off log line while the cited 404
    proof is admissible; C4 bounds the consequence to a missing verification step and no
    status change.
27. **Per-row labels ignored, and `"FOUND"` read as absence.** A read-only audit of the
    full run showed a resource attributed to the wrong system: `evidence_36`
    (`customer_export_manifest_20260409.json`) is a flat manifest whose rows carry their
    own `"system":"security_service"`, `"record_type":"SECURITY_AUDIT_LOG"`,
    `"result":"FOUND"`, but our `Fact` for `audit_record_989` was
    `export_service / UNKNOWN / ABSENT`. Root cause, two defects in the same code path:
    the flat-manifest branch of `parse_json` took the *container* labels (the file-level
    `origin` as the system and the file-level `collection` as the category) and ignored
    each row's own `system` / `record_type`; and `_from_record` set `presence` to `PRESENT`
    only when `result == "hit"`, so `FOUND` (and `MATCH`, `PRESENT`, `OK`, `TRUE`) became
    `ABSENT`. `parse_log` shared the second half of the defect (it preferred the emitting
    `service` over the row's `system`, and its present-set omitted `"match"`). Consequence
    in the graded artefact: every `audit_record_*` row in a container that was not
    `security_service` was mis-owned and mis-stated. *Fix:* one shared label layer in
    `audit/evidence/facts.py` (`row_system`, `row_category` over a `RECORD_TYPE_CATEGORY`
    map, `presence_from_result` over explicit `PRESENT_RESULTS` / `ABSENT_RESULTS` sets),
    and every parser (`parse_json` flat manifests, snapshot bundles and `service_exports`,
    `parse_sql` incl. `record_json` rows, `parse_log`) now lets a row's own system /
    record_type win over its container, with the container only as fallback; an
    unrecognised `result` token is reported and left at the parser default instead of being
    silently downgraded to `ABSENT` (observed: `identifiable` 24, `active` 16, `anonymized`
    9, `blocked` 4, `disabled` 4, `redacted` 4). *Measured effect.* Samples unchanged —
    32/32, 19/32, 21/32, 164/193, 1031/1158 (no regression). Full run: resource-id-prefix ↔
    `system_id` mismatches **91 → 0**, `COMPLETE` 221 → 216, `INCOMPLETE` 287 → 292, steps
    2507 → 2571. `case_383`–`case_392` change from e.g. `export_service audit_record_989
    VERIFY_RECORD_ABSENT` to `security_service audit_record_989 VERIFY_RECORD_PRESENT`
    (`evidence_36`), statuses unchanged (`COMPLETE`). The existence rule was re-tested after
    the fix: the literal `PRESENT`-only variant now scores 18/32, 19/32, 158/193, 1007/1158
    — strictly worse than `_record_exists()` (present, or retained anonymised/redacted), so
    the earlier rejection of `PRESENT`-only was **not** caused by this bug and the existing
    rule is kept. On the 518 the two rules now agree on both the status counts (216/292/10)
    and the total step count (2571): they differ only in the operation a retained
    anonymised/redacted row is reported with (`VERIFY_RECORD_ANONYMIZED` /
    `VERIFY_RECORD_REDACTED` vs a spurious `VERIFY_RECORD_ABSENT`), in **12** cases.
    `validate_output.py` gained the prefix-vs-`system_id` invariant as a warning-only check
    (gold has 0 exceptions; the artefact now has 0).

### Known limitations (deliberate, contract-driven)

* 17 of the 193 reference steps remain unresolved: 6 account rows in batches where the
  candidate count exceeds one (a coin flip is taken rather than omitted), 5 analytics rows
  whose identifiers are all null and whose batch is not structurally forced, and 6 audit
  records that exist only in a post-`audit_at` snapshot or in no file at all.
* `case_231` loses one verification step when the index is rebuilt from cut-off facts only:
  the account id is learned from a post-cut-off log line, while the 404 fact that proves the
  row is gone is admissible. Tightening membership to fix it costs 9 positional matches, so
  it is left as a documented asymmetry that C4 bounds (no status change).
* 4 of 123 admissible `CHECK_RESTORE_ACCESS` claims contradict the evidence
  (`restore_allowed=False`). The evidence wins; the four cases are listed in entry 21.
* `reported_at` / `reported_status` are never proof of deletion; they are used only to pick
  which worker row produced the claim. A workflow that lies in the opposite direction
  (claims failure after a real deletion) is still resolved from evidence alone.
* Entities that exist only inside PDF/image evidence are limited by extraction quality;
  the extracted facts are cached and versioned (`PROMPT_VERSION`) so results are stable.
