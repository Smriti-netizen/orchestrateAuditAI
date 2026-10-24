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
| Step match, positional (all six fields equal) | 162 / 193 = 83.9 % |
| Field-level accuracy, positional (6 fields × 193 steps) | 1029 / 1158 = 88.9 % |

Status distribution produced for the same 32 cases: `COMPLETE` 10, `INCOMPLETE` 22,
`NOT_APPLICABLE` 0 — identical to the reference distribution.

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
| Absence proof snapshot choice | `case_21/25 account_6440/3136` | 2 evidence ids | both are valid covering snapshots; the reference cites the later bundle |
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

The two differences are the absence proof for a provenance-resolved account
(`case_21 account_6440`, `case_25 account_3136`): the reference cites the later bundle
`evidence_92`, our rule cites the latest covering snapshot that predates it
(`evidence_07`). Both snapshots omit the row, so both prove absence; only the tie-break
between equally valid snapshots differs.

#### Ordering and extra steps

There are **no order inversions** and no wrong operations or states. Five predicted steps
are extra because they name the wrong half of an ambiguous account batch (the 5 wrong ids
in the table above); every other case is the reference sequence minus omitted verification
steps. `build_steps` sorts findings by operation rank, then system/resource, and
verifications by the contract's operation order, which matches the reference on 32/32 cases.

---

## 3. Participant-authored evaluation cases

Implemented as property tests in `code/evaluation/custom_cases.py` so they do not depend
on the sample answers. Current result: **3 / 3 pass**.

| Case | What it asserts | Why it matters | Observed |
| --- | --- | --- | --- |
| **C1 — authorization gate** | The same subject that produces real deletion steps (`leila.haddad@yahoo.com` → 1 step) must produce `NOT_APPLICABLE` / `[]` when the text is "send me a copy … do not change anything". | The gate must be driven by `request_text`, not by the presence of data. A bug here silently destroys true positives. | `NOT_APPLICABLE`, 0 steps, control produced 1 step ✓ |
| **C2 — historical-email resolution** | A request sent from a superseded address (`o.walker6695@yahoo.com`) must resolve to the same subject and produce byte-identical steps as the current address. | Identity is reconstructed from the current snapshot **plus** the audit log; old identifiers still appear in older systems. | both → `user_523`, both `INCOMPLETE`, 4 vs 4 identical steps ✓ |
| **C3 — multilingual deletion request** | A Portuguese "excluir a minha conta … apaguem tudo" must be an authorized deletion. | Requests arrive in several languages; an English-only classifier would mark them `NOT_APPLICABLE`. | authorized ✓, `COMPLETE`, 1 step ✓ |

---

## 4. Full-dataset run (the graded artefact)

| Item | Value |
| --- | --- |
| Rows written | **518** (one per `case_id`, no duplicates, sorted by `case_id`) |
| `COMPLETE` | 211 |
| `INCOMPLETE` | 297 |
| `NOT_APPLICABLE` | 10 (all with `steps_json = []`) |
| Total steps | 2554 (avg 4.93 per case) |
| Unresolved subjects | 0 |
| Provenance-linked account rows | 342 (each a distinct account, 0 reused; 176 cases omitted) |
| Batch-linked analytics rows | 54 (132 batches reported ambiguous) |
| Structural validation | consecutive `step_no` from 1 ✓, phase order FINDING → REMEDIATION → VERIFICATION ✓, every step has exactly the seven contract fields ✓ |

Operation totals across the run: `REPORT_REMAINING_RECORD` 329, `REPORT_EXPOSED_PII` 149,
`REPORT_ACTIVE_DATA_FLOW` 69, `REPORT_RESTORE_RISK` 11, `DELETE_RECORD` 329,
`REDACT_RECORD` 92, `ANONYMIZE_RECORD` 57, `DISABLE_DATA_FLOW` 69, `BLOCK_RESTORE` 11,
`VERIFY_RECORD_ABSENT` 790, `VERIFY_RECORD_PRESENT` 253, `VERIFY_RECORD_REDACTED` 146,
`VERIFY_RECORD_ANONYMIZED` 96, `VERIFY_DATA_FLOW_DISABLED` 106,
`VERIFY_RESTORE_BLOCKED` 47. Every remediation operation has a matching verification
operation with at least the same count, which is the internal consistency invariant the
planner guarantees.

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
    metrics restored to 19/32 exact, 162/193 positional.

### Known limitations (deliberate, contract-driven)

* 17 of the 193 reference steps remain unresolved: 6 account rows in batches where the
  candidate count exceeds one (a coin flip is taken rather than omitted), 5 analytics rows
  whose identifiers are all null and whose batch is not structurally forced, and 6 audit
  records that exist only in a post-`audit_at` snapshot or in no file at all.
* `reported_at` / `reported_status` are never proof of deletion; they are used only to pick
  which worker row produced the claim. A workflow that lies in the opposite direction
  (claims failure after a real deletion) is still resolved from evidence alone.
* Entities that exist only inside PDF/image evidence are limited by extraction quality;
  the extracted facts are cached and versioned (`PROMPT_VERSION`) so results are stable.
