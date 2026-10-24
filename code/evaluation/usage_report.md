# Usage report — final full-dataset run

Artefact: root-level `output.csv` (518 rows), produced by

```bash
python3 code/main.py --no-llm
```

on the supplied `dataset/deletion_requests.csv`.

---

## 1. Where model calls happen

The agent is **deterministic by design**: every decision, step ordering, evidence choice
and JSON serialisation is rule-based stdlib Python. Model calls are used for exactly one
job — turning the 11 PNG screenshots into normalised records at index-build time. The four
PDFs are parsed locally (no tokens). Each extraction is cached to
`code/media_extractions/{file_id}.json` under a sha256 of the file plus `PROMPT_VERSION`, so it
is paid for once and reused byte-identically afterwards.

Consequently the graded full-dataset run performs **0 new model calls**: all 11 images hit
the cache (11 media cache entries present, 0 cache misses during the run). The token spend
below is the cost of building that cache, i.e. the complete cost of producing `output.csv`
from a clean checkout.

### Which run populated the media cache

The extraction cache under `code/media_extractions/` was written by the **initial media-extraction run on
2026-10-24** (11 entries, timestamps 20:49–20:56 local). That is the run metered in
`code/cache/usage.json`: 32 `qwen/qwen3.8-27b` vision calls (73 582 input / 14 976 output
tokens) plus 1 `openai/gpt-oss-20b` text smoke call (74 / 5 tokens) — 33 calls in total.
Every later run, including the final 518-case run that produced the submitted `output.csv`,
reuses those 11 entries: **0 model calls, 0 new tokens, 0 cache misses**. The per-request
figures in §5 therefore attribute the whole one-off cache cost to the 518 requests.

## 2. Providers and models

| Provider | Endpoint | Model | Role |
| --- | --- | --- | --- |
| Groq | OpenAI-compatible chat completions | `qwen/qwen3.8-27b` | vision extraction of the 11 PNG evidence screenshots |
| Groq | OpenAI-compatible chat completions | `openai/gpt-oss-20b` | single connectivity/schema smoke test of the text path |

Temperature 0, one image per call, small batches, sha256 response cache, 429 /
`retry-after` backoff with a hard stop on daily quota exhaustion.

## 3. Metered totals (source: `code/cache/usage.json`)

| Model | Calls | Input tokens | Output tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: |
| `qwen/qwen3.8-27b` | 32 | 73 582 | 14 976 | 88 558 |
| `openai/gpt-oss-20b` | 1 | 74 | 5 | 79 |
| **Overall** | **33** | **73 656** | **14 981** | **88 637** |

Cache hits during the graded run: 11 media extractions reused, 0 model calls re-issued.

## 4. Cost (list prices from the model catalogue, USD per token)

| Model | Input $/token | Output $/token | Input cost | Output cost | Model total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qwen/qwen3.8-27b` | 0.0000008 | 0.000004 | $0.058866 | $0.059904 | **$0.118770** |
| `openai/gpt-oss-20b` | 0.000000075 | 0.0000003 | $0.0000056 | $0.0000015 | **$0.0000071** |
| **Overall** | | | | | **≈ $0.118777** |

## 5. Per-request figures (518 requests)

| Measure | Value |
| --- | ---: |
| Total tokens / requests | 88 637 / 518 |
| Average tokens per request | **171.1** |
| Average input tokens per request | 142.2 |
| Average output tokens per request | 28.9 |
| Total estimated cost | **$0.1188** |
| Average cost per request | **$0.00023** |
| Average model calls per request | 0.064 (33 calls for 518 requests) |

These averages are conservative: the spend is a one-off fixture-extraction cost, so a
second run on the same dataset costs $0.00 while producing an identical `output.csv`.

## 6. Guardrails that keep the spend bounded

* One image per call, `max_tokens=800`, `MAX_RETRIES=6` with exponential backoff.
* A persistent usage meter (`UsageMeter`) records calls and tokens per model, and a
  hard stop aborts the run when the provider reports an exhausted daily quota rather
  than burning retries.
* Every response is content-addressed on disk, so re-runs and re-evaluations never
  re-pay for the same bytes.
* `--no-llm` disables the model path entirely; the graded run uses it because every
  media artefact is already cached, which is what makes the output reproducible.

No API keys, credentials or sensitive configuration values are recorded in this report;
the key is read from the uncommitted repository-root `.env` at runtime only.
