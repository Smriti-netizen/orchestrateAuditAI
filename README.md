# HackerRank Orchestrate - October 2026

## Ghost Data Still Exists

Build an AI agent that audits whether a user's personal data was actually deleted.

A deletion workflow may report **Complete** while data still exists in databases, logs, support attachments, exports, search indexes, archives, or backups. Sync and restore processes may also bring deleted data back.

For each request, the agent must first determine whether deletion was actually requested. It then returns `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`, reports any remaining data or recreation risk, and produces the ordered remediation and verification steps required to finish an authorized deletion safely.

Read [`problem_statement.md`](./problem_statement.md) for the complete challenge contract.

---

## Quick Start

Clone the repository and move into the project directory:

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26.git
cd hackerrank-orchestrate-october26
```

Start your AI coding tool from the **repository root** so it automatically reads [`AGENTS.md`](./AGENTS.md) and logs your session turns to `log.txt` in the root directory.

Build your solution in `code/main.py`, or use another language and document its setup and run command clearly in your README.

Your solution must:

- Read the input files from `dataset/`
- Generate one prediction row for every request in `dataset/deletion_requests.csv`
- Write the final predictions to `output.csv` in the repository root

Run the starter Python entry point with:

```bash
python3 code/main.py
```

The blank template at `dataset/output.csv` is provided as a reference. Your final generated file for submission must be the root-level `output.csv`.


## Solution (this submission)

A deterministic deletion-audit agent in `code/` (Python 3, standard library only; pypdf
optional for PDFs with no effect on output, model is an optional cached extractor not part
of the decision path). For live-interview prep, see [`INTERVIEW.md`](./INTERVIEW.md) at the
repository root (not part of `code.zip`).

### Setup

```bash
# from the repository root, Python 3.10+
python3 code/main.py            # writes ./output.csv for all 518 requests
```

Nothing to install: no third-party packages are required. An uncommitted `.env` in the
repository root is only needed when you want to (re)extract the PNG evidence:

```text
LLM_PROVIDER=groq
LLM_API_KEY=<your key>
```

The key is read from the environment at runtime, never printed, logged or cached. The
11 screenshots are already extracted into `code/media_extractions/`, so a normal run makes
**zero** model calls and is fully reproducible offline.

### Commands

| Command | Purpose |
| --- | --- |
| `python3 code/main.py` | full run → `output.csv` (518 rows, sorted, deterministic) |
| `python3 code/main.py --no-llm` | same, with the model path disabled (used for the graded run) |
| `python3 code/main.py --cases dataset/sample_deletion_requests.csv --out /tmp/pred.csv --no-llm` | calibration run on the 32 public cases |
| `python3 code/main.py --out /tmp/pred.csv --eval` | score a file against `dataset/sample_output.csv` (status, exact, sequence, positional, field) |
| `python3 code/evaluation/validate_output.py output.csv` | structural contract check (518 rows, numeric `case_id` order, JSON steps, `NOT_APPLICABLE` rule) |
| `python3 code/evaluation/custom_cases.py` | participant-authored property tests (5 cases; expect 5/5) |
| `python3 code/main.py --m1` / `--m2` / `--link` | diagnostics: evidence parse, identity resolution, resource linking |
| `python3 code/main.py --models` / `--choose` / `--test` | inspect the model catalogue, pick a model, ping it |

### Architecture walkthrough (one page)

```
dataset/*.csv ──► audit/loaders.py          typed rows: requests, identities,
                                            audit log, activity, rules
dataset/evidence/* ──► audit/evidence/*     one Fact per normalised record
      parse_sql.py    SQL snapshots         (SQLite in-memory, primaries + replicas)
      parse_json.py   bundles + exports     (rows, hits.documents, 404 lookups)
      parse_log.py    logs                  (trace stitching, event timestamps)
      parse_text.py   handover notes
      media.py        PDFs / PNGs           (PDF text locally, PNG via cached model)
                       │
                       ▼
            FactIndex  (file_id, captured_at, system_id, resource_id,
                        category, presence, identifiers, extra, topology, covers)
                       │
      ┌────────────────┼──────────────────────────────┐
      ▼                ▼                              ▼
 identity.py        link.py                       coverage
 current snapshot   identifier-only linking       which snapshot enumerates a
 + audit-log        + LINK_RESOURCE_IDENTITY      whole collection at what time
 history            + source_resource_id
                    + connector/backup rows
                    + same-trace_id stitching
      │                │                              │
      └────────────────┴──────────────┬───────────────┘
                                      ▼
                              engine.py
        ┌─────────────────────────────────────────────────────┐
        │ 1. authorization: request_text classifier           │
        │    (negation markers → deletion stems → multilingual)│
        │ 2. admissibility: manifest captured_at ≤ audit_at;   │
        │    log lines judged by their own event timestamp;    │
        │    pre-request absence observations dropped          │
        │ 3. state: present in ANY replica is remaining        │
        │ 4. policy: deletion_rules.csv → DELETE / REDACT /    │
        │    ANONYMIZE / RETAIN / TOMBSTONE                    │
        │ 5. planning: FINDING → REMEDIATION → VERIFICATION,   │
        │    stable sort (operation order, system, resource)   │
        │ 6. evidence: findings cite a file_id; remediations   │
        │    and post-remediation checks use ""                │
        └─────────────────────────────────────────────────────┘
                                      ▼
                              output.csv
```

Key invariants (each replaced a measured guess):

* **No proximity, no naming conventions.** Resources link only through exact identifiers,
  explicit identity-link events, `source_resource_id`, connector/backup rows, or shared
  `trace_id` log lines — never through names (3 shared-name decoys, 0 false links).
* **Account provenance (ranked one-to-one resolver).** The worker's account
  `DELETE_RECORD` row has no identifier. `audit/account_link.py` builds candidate tiers
  (claim-compatible `DELETE_RECORD` on `account_service`, inside `[requested_at, audit_at]`,
  within ±2 h of `reported_at`, excluding rows already owned by another subject). Candidates
  are assigned **one-to-one** at confidence **1/k** per batch; cases are served best-first;
  picks below a **0.25** floor (chosen by **17/17** leave-one-out folds) are **omitted**.
  Measured on 518 requests: **342/518** resolve to **342 distinct** account rows (**0**
  reused); **176** cases get no provenance account. **Trade-off:** an omitted account step
  is a guaranteed miss on that resource, while a wrong guess can occasionally hit the
  reference — but a provenance guess may attach only that row's **ABSENT** facts, so it can
  prove absence, never create a remaining-record finding; **deletion_status is unchanged**
  by a guess either way. Batch-order and anchor-adjacency mappers were rejected first
  (precision **0.25–1.00** on 2–11 picks).
* **Analytics batch rule (structurally forced batches only).** Anonymous `analytics_*`
  rows in a worker batch are linked only when the batch has **k ≥ 2** anonymous rows and
  exactly **k** cases with no identifier-linked analytics resource; pairing follows
  activity order → numeric `case_id` order (**2/2** on the 32 samples, **54** links on
  518; **132** batches reported ambiguous and left unlinked).
* **`_record_exists` (not literal `PRESENT` only).** Existence means `presence == PRESENT`
  **or** the row is a retained anonymised/redacted form (`anonymized` / `redacted` flags).
  The literal rule `record_exists = (presence == PRESENT)` was measured and **rejected**:
  it regressed the 32 samples to **18/32** exact, **158/193** positional (it labels a
  retained anonymised/redacted row `VERIFY_RECORD_ABSENT`). The principled helper keeps the
  samples at **21/32** exact, **174/193** positional; on the full run both rules give the
  same status counts (**216/292/10**, **2571** steps) and differ only in the operation/state
  of the retained rows in **12** cases.
* **Membership at `audit_at`.** A resource enters the plan only with an admissible fact or
  an in-window activity/provenance link. **158** `(case, resource)` pairs were tied only
  by post-`audit_at` snapshots and were removed (**−22** steps, **0** status flips).
  **`_superseded()`** retires a stale `PRESENT` row when a fresher same-collection,
  same-topology snapshot exists at/before `audit_at` — **0** pairs on this dataset (all
  **1113** candidate supersede snapshots fall after every case's `audit_at`); kept for the
  synthetic post-audit fixture in `code/evaluation/cases/`. A **per-stream "newest row"**
  citation rule and **CHECK_RESTORE_ACCESS** claim override were measured and **rejected**
  (broke `case_09` evidence; **4/123** restore claims contradict facts).
* **Null tokens are not identifiers.** Literal `null` / `none` / `n/a` in logs are dropped
  before identifier classification.
* **Claims are never proof.** Workflow statuses and `CLASSIFY_REQUEST` rows are reported
  only via `classify_disagreements()`; state comes from evidence.
* **Absence needs coverage.** `VERIFY_RECORD_ABSENT` requires a covering snapshot at/before
  `audit_at` that enumerates the collection.
* **Replica drift.** Present in **any** replica topology counts as present; drift is a
  finding, not deletion.
* **Policy → operation.** Categories map to DELETE / ANONYMIZE / REDACT / RETAIN per
  `deletion_rules.csv`; finance records are out of scope.
* **Determinism and ordering.** Stable sorts, temperature 0, `--no-llm` graded run; rows
  written in **numeric `case_id` order** (`case_33` before `case_100`), enforced by
  `validate_output.py`.

### Design decisions

| Theme | Choice | Number that justified it |
| --- | --- | --- |
| Core engine | Deterministic stdlib Python; LLM only for 11 PNGs (cached in `code/media_extractions/`) | **0** model calls on final 518 run; **33** calls / **≈ $0.119** one-off cache build (`usage_report.md`) |
| Linking | Exact identifiers only | **0** email/phone/hash collisions; **0** ambiguous resolves |
| Replica / time | Any replica `PRESENT` counts; facts need `captured_at ≤ audit_at` | **158** post-cut-off pairs removed |
| Policy | Rule table → operation mapping | Categories drive DELETE/REDACT/ANONYMIZE/RETAIN steps |
| Ordering | Phase → operation rank → `system_id` → `resource_id` | Positional scoring on 32 samples |
| Account | Ranked 1:1 resolver with 0.25 floor | **342/518** resolved, **0** reuse, **176** omitted |
| Analytics | Structurally forced batches only | **54** links; ambiguous batches skipped |
| `_record_exists` | PRESENT ∨ anonymized ∨ redacted | Rejected literal PRESENT-only: **18/32** exact, **158/193** positional |
| Omission vs guess | Omit low-confidence provenance | LOO floor **0.25**; wrong guess cannot invent findings |
| Packaging | `media_extractions/` outside `code/cache/` | Zip + `--no-llm` reproduces `output.csv` byte-identically |

### Repository layout

```
code/
  main.py                 CLI, evidence index build, generate / eval / diagnostics
  audit/
    config.py             path resolution (works from any CWD, any OS)
    loaders.py            typed CSV loaders
    identity.py           current + historical identifier sets, exact resolution
    link.py               identifier-only linking, snapshot coverage, absence citation
    engine.py             state reconstruction, policy, planning, classifier
    media.py              PDF/PNG extraction and caching
    llm.py                Groq client, rate limiter, usage meter, disk cache
    evidence/             facts.py + per-format parsers
  media_extractions/      cached screenshot extractions (shipped; no network at run time)
  cache/                  model catalogue and usage meter
  evaluation/
    cases/                C4-C5 property tests (post-audit cut-off, retention + flow)
    custom_cases.py       participant-authored property tests (run standalone; 5/5)
    validate_output.py    structural validator for root `output.csv`
    evaluation_report.md  metrics, custom cases, failures and fixes
    usage_report.md       providers, models, calls, tokens, cost
```

Root `output.csv` is sorted by **numeric** `case_id` (not lexicographic). The 11 screenshot
extractions live under `code/media_extractions/` (committed, **outside** `code/cache/` so the
documented zip exclusion `*/cache/*` cannot drop them).


## Dataset

All participant-facing files are in `dataset/`:

| File | Contents |
| --- | --- |
| `deletion_requests.csv` | 518 requests |
| `sample_deletion_requests.csv` | 32 sample requests |
| `sample_output.csv` | Correct sample answers |
| `user_identities.csv` | Current user identities |
| `user_audit_log.csv` | Historical identity changes |
| `system_activity.csv` | Activity across services and data processes |
| `deletion_rules.csv` | Required treatment for each data category |
| `evidence_files.csv` | Manifest of supplied evidence files |
| `output.csv` | Blank output template |

Evidence is stored under `dataset/evidence/` and may include PDFs, images, logs, JSON, plain text, or SQL snapshot exports. SQL captures may contain a primary database, regional replicas, a reporting replica, or a failover replica so replication drift can be investigated directly. The `.sql` files are readable as text and executable with SQLite.

Only `deletion_requests.csv` requires answers. The other files provide evidence and context.

---

## Output

Create `output.csv` with these columns in this exact order:

```text
case_id,deletion_status,steps_json
```

Produce exactly one row per request. `deletion_status` is `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`. `steps_json` is an ordered JSON array containing findings, remediation actions, and verification checks. Use an empty array (`[]`) for `NOT_APPLICABLE`. Evidence references may be a `file_id`, `activity_id`, or `audit_id`. Determine the resource state at `audit_at` from the available evidence; reported workflow statuses and operation results are claims. Use `dataset/sample_output.csv` as the format reference.

The final file must contain 518 data rows sorted by `case_id` (plus the header row). The grader parses `steps_json` as JSON, so object-key order and whitespace do not matter; array order does. Order findings, remediation, and verification in that phase order. Within a phase, follow the operation order in the problem statement, then sort by `system_id` and `resource_id`.

---

## Evaluation

`output.csv` will be compared with hidden canonical answers. Scoring considers:

- deletion assessment accuracy;
- current and historical identity resolution;
- discovery of remaining data and recreation risks;
- correct policy application and evidence references; and
- completeness and ordering of remediation and verification steps.

Your `code.zip` must include:

```text
code/evaluation/evaluation_report.md
code/evaluation/usage_report.md
```

- `evaluation_report.md` must cover case, row, field, and sequence accuracy, at least three additional evaluation cases created by you, failures found, and improvements made.
- `usage_report.md` must cover the final full-dataset run, including model providers and names, model calls, input and output tokens, total and average tokens per request, and estimated total and per-request cost. Include per-model and overall totals when using multiple models.

**Important:** Place `evaluation_report.md` and `usage_report.md` inside `code/evaluation/` (matching `code/evaluation/main.py` in this starter layout), write your complete evaluation metrics, custom evaluation test cases, and token/cost breakdowns directly inside those Markdown files, and keep prompts in source code or Markdown files. Automated code evaluation ignores raw data files (`.csv`, `.tsv`, `.json`, `.jsonl`, `.sql`, `.log`), so content stored only in those formats will not be visible to the code judge or AI interviewer.

---

## Chat Transcript

This repository includes [`AGENTS.md`](./AGENTS.md), which instructs compatible AI coding tools to append conversation summaries to `log.txt` in the repository root:

| Platform | Path |
| --- | --- |
| macOS / Linux | `<repo root>/log.txt` |
| Windows | `<repo root>\log.txt` |

- Always launch your AI coding agent from the repository root (or explicitly ask it to read `./AGENTS.md` first) so every turn is logged to `log.txt` with the required `User Prompt`, `Agent Response Summary`, `Actions`, and `Context` sections.
- Upload `log.txt` directly as your `chat_transcript` submission on HackerRank.
- Do not include API keys, tokens, credentials, or secrets in prompts, code, configuration, or `log.txt`.

---

## Packaging `code.zip` and Submission

| Item | Description |
| --- | --- |
| `code.zip` | Runnable solution, prompts, configuration, `README.md`, and `evaluation/` reports |
| `output.csv` | One answer row for each of the 518 requests in `dataset/deletion_requests.csv` |
| `chat_transcript` | The root `log.txt` file generated by your coding agent |
| AI interview | Live explanation of your architecture, approach, and evaluation |

You may use any programming language, model, framework, or retrieval approach. Include clear setup and execution instructions in `README.md` inside `code.zip`.

To package `code.zip` from the repository root without bundling the dataset:

**macOS / Linux:**

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store" ".env" "*.env" "*/.env" "*/.env.*" "*/cache/*"
```

**Windows (PowerShell):**

```powershell
# .env, caches and bytecode live outside the archived set, so they are never bundled.
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
Get-ChildItem code -Recurse -Include "__pycache__",".env","*.env" -Force |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
```

When packaging `code.zip`:

- Keep your evaluation reports in `code/evaluation/` so they are bundled automatically with `code` (or add `evaluation` to the archive command above if you placed them in a top-level `evaluation/` directory).
- Never include `.env` or any other credentials file: the API key is read from the repository-root `.env` at runtime only, it is listed in `.gitignore`, and the archive commands above exclude it (only `README.md` and `code/` are added).
- Do **not** include `dataset/`, virtual environments (`.venv`, `venv`, `node_modules`), or build caches (`__pycache__`). On Windows, keep `.venv` at the repository root (outside `code/`) and remove any `code/__pycache__` folders before running `Compress-Archive`.
- Do **not** include nested archives (`.zip`, `.tar`, `.gz`, `.tgz`, `.7z`, `.rar`) inside `code.zip` — submissions containing nested archive files will be rejected.
- Keep `code/media_extractions/` **inside** the archive. It holds the 11 committed screenshot extractions; it deliberately sits outside `code/cache/` so the cache exclusion above cannot drop it, and a `--no-llm` run needs it to reproduce `output.csv` byte for byte. Verified by unzipping `code.zip`, adding `dataset/`, and diffing a fresh run against the submitted file.

Before submitting, confirm that:

- `output.csv` has the exact header `case_id,deletion_status,steps_json` and contains all 518 data rows sorted by `case_id`;
- every `steps_json` value is a valid JSON array with consecutive step numbers starting at `1`;
- findings cite valid evidence IDs, and `COMPLETE` verification steps cite evidence proving the current state;
- proposed remediation and future verification steps use an empty `evidence_id` (`""`);
- `code.zip` includes your runnable code, `README.md`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`;
- `log.txt` follows the `AGENTS.md` turn format and is uploaded to the `chat_transcript` slot; and
- no API keys, credentials, real personal data, or sensitive configuration values are included.
