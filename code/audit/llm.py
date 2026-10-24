"""Groq (OpenAI-compatible) client: .env loading, rate limiting, disk cache, usage meter.

Design constraints:
  * stdlib only (urllib) - no extra dependency in the submission.
  * every successful response is cached by sha256(model + prompt + input), so a
    rerun after the daily quota is exhausted replays from cache.
  * a hard stop raises QuotaExhausted instead of hammering the API.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"
CACHE_DIR = REPO_ROOT / "code" / "cache" / "llm"
USAGE_PATH = REPO_ROOT / "code" / "cache" / "usage.json"

BASE_URL = "https://api.groq.com/openai/v1"

# Conservative free-tier pacing: ~30 requests/minute.
MIN_INTERVAL_SECONDS = 2.1
MAX_RETRIES = 6

# Model catalog is fetched once from /models and cached here (ids, modalities, prices).
CATALOG_PATH = REPO_ROOT / "code" / "cache" / "model_catalog.json"


class QuotaExhausted(RuntimeError):
    """Raised when the provider reports the daily quota is gone. Stop calling."""


class LLMError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def load_env(path=None):
    """Parse repo-root .env into os.environ. Real env vars always win."""
    path = Path(path) if path else ENV_PATH
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def api_key():
    load_env()
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise LLMError(0, "LLM_API_KEY is not set (expected in repo-root .env)")
    return key


def _request(path, payload=None, method=None):
    url = BASE_URL + path
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "ghost-data-audit/1.0",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise LLMError(exc.code, body[:500]) from None


def _cache_key(model, messages, extra):
    blob = json.dumps({"model": model, "messages": messages, "extra": extra},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class UsageMeter:
    """Calls and tokens per model, persisted so usage_report.md is reproducible."""

    def __init__(self, path=None, prices=None):
        self.path = Path(path) if path else USAGE_PATH
        self.prices = prices or {}
        self.data = {"by_model": {}, "cache_hits": 0, "calls": 0}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.data.setdefault("by_model", {})
        self.data.setdefault("cache_hits", 0)
        self.data.setdefault("calls", 0)

    def record(self, model, prompt_tokens=0, completion_tokens=0):
        bucket = self.data["by_model"].setdefault(
            model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        bucket["calls"] += 1
        bucket["input_tokens"] += int(prompt_tokens or 0)
        bucket["output_tokens"] += int(completion_tokens or 0)
        self.data["calls"] += 1
        self.save()

    def record_cache_hit(self):
        self.data["cache_hits"] += 1
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")

    def summary(self):
        rows, total_cost = [], 0.0
        for model, b in sorted(self.data["by_model"].items()):
            price = self.prices.get(model)
            cost = None
            if price:
                cost = b["input_tokens"] * price.get("prompt", 0) + \
                       b["output_tokens"] * price.get("completion", 0)
                total_cost += cost
            rows.append({"model": model, **b, "usd": cost})
        return {"by_model": rows, "cache_hits": self.data["cache_hits"],
                "calls": self.data["calls"], "usd_total": total_cost or None}


class LLMClient:
    def __init__(self, model=None, cache_dir=None, usage=None, sleeper=time.sleep):
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.usage = usage or UsageMeter()
        self._sleep = sleeper
        self._last_call = 0.0
        self._quota_dead = False

    # ---------- model discovery ----------
    def list_models(self):
        data, _ = _request("/models")
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    # ---------- core call ----------
    def chat(self, messages, model=None, max_tokens=1024, temperature=0.0, extra=None):
        model = model or self.model
        if not model:
            raise LLMError(0, "no model selected")
        if self._quota_dead:
            raise QuotaExhausted("daily quota exhausted earlier in this run; "
                                 "rerun later to resume from cache")

        extra = dict(extra or {})
        key = _cache_key(model, messages, {"max_tokens": max_tokens,
                                           "temperature": temperature, **extra})
        cached = self._read_cache(key)
        if cached is not None:
            self.usage.record_cache_hit()
            return cached

        payload = {"model": model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature, **extra}
        delay = MIN_INTERVAL_SECONDS
        last = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                data, headers = _request("/chat/completions", payload)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
                usage = data.get("usage") or {}
                self.usage.record(model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
                self._write_cache(key, text, model)
                return text
            except LLMError as exc:
                last = exc
                if exc.status == 429:
                    self._handle_429(exc, attempt)
                    delay = min(delay * 2, 60)
                    continue
                if exc.status in (500, 502, 503, 504):
                    self._sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise
        raise last or LLMError(0, "request failed")

    def _throttle(self):
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def _handle_429(self, exc, attempt):
        body = str(exc).lower()
        daily = any(token in body for token in (
            "per day", "daily", "tokens per day", "tpd", "quota exceeded", "insufficient_quota"))
        m = re.search(r"(?:retry[- ]after[\"']?\s*[:=]\s*|try again in\s+)([\d.]+)\s*s?", body)
        retry_after = float(m.group(1)) if m else None
        if daily and attempt >= 1:
            self._quota_dead = True
            raise QuotaExhausted(
                "Groq daily quota exhausted - stopping cleanly. "
                "Cached results were kept; the next run resumes from cache.")
        if retry_after is not None and retry_after > 120:
            self._quota_dead = True
            raise QuotaExhausted(
                f"Groq asked to retry after {retry_after:.0f}s (quota window). Stopping cleanly; "
                "the next run resumes from cache.")
        wait = (retry_after + 0.5) if retry_after is not None else min(30, 2 ** (attempt + 1))
        self._sleep(wait)

    # ---------- cache ----------
    def _cache_path(self, key):
        return self.cache_dir / key[:2] / f"{key}.json"

    def _read_cache(self, key):
        p = self._cache_path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("text")
        except Exception:
            return None

    def _write_cache(self, key, text, model):
        p = self._cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"model": model, "text": text}, ensure_ascii=False),
                     encoding="utf-8")


# ---------- model catalog ----------

def fetch_catalog():
    """Live model metadata: id, modalities and per-token prices."""
    data, _ = _request("/models")
    out = []
    for m in data.get("data", []):
        if not m.get("id"):
            continue
        out.append({
            "id": m["id"],
            "input_modalities": m.get("input_modalities") or [],
            "output_modalities": m.get("output_modalities") or [],
            "pricing": m.get("pricing") or {},
            "context_window": m.get("context_window"),
        })
    return out


def load_catalog(refresh=False):
    """Cached catalog so a rerun does not spend a request on /models."""
    if CATALOG_PATH.exists() and not refresh:
        try:
            return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    catalog = fetch_catalog()
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return catalog


def prices_by_model(catalog):
    return {m["id"]: m.get("pricing") or {} for m in catalog}


# ---------- task helpers ----------

def pick_text_model(catalog):
    """Cheapest general-purpose chat model, ranked by live prompt+completion price.

    Guards/transcribers/TTS are excluded: they are cheaper but not chat models.
    """
    non_chat = ("guard", "whisper", "orpheus", "safeguard", "embed", "rerank")
    usable = [m for m in catalog
              if "text" in m["input_modalities"] and "text" in m["output_modalities"]
              and m.get("pricing") and m["pricing"].get("prompt") is not None
              and not any(tag in m["id"].lower() for tag in non_chat)]
    if not usable:
        return None

    def cost(m):
        p = m["pricing"]
        return (float(p.get("prompt") or 0), float(p.get("completion") or 0), len(m["id"]))

    return sorted(usable, key=cost)[0]["id"]


def pick_vision_model(catalog):
    """Any model that genuinely accepts image input (per provider metadata)."""
    usable = [m for m in catalog
              if "image" in m["input_modalities"] and "text" in m["output_modalities"]]
    if not usable:
        return None
    return sorted(usable, key=lambda m: float((m.get("pricing") or {}).get("prompt") or 0))[0]["id"]


def classify_batch(client, model, texts, labels):
    """One batched intent-classification call -> list of labels (same order)."""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    system = ("You classify short customer privacy requests. Reply with one label per line "
              "as '<n>: <label>'. Allowed labels: " + ", ".join(labels) + ". "
              "Never explain. Keep the input numbering.")
    out = client.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": numbered}],
        model=model, max_tokens=20 * len(texts) + 40, temperature=0.0)
    result = [None] * len(texts)
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.)-]\s*([a-zA-Z_]+)", line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts):
                label = m.group(2).upper()
                if label in labels:
                    result[idx] = label
    return result


if __name__ == "__main__":
    import sys
    client = LLMClient()
    if "--models" in sys.argv:
        for mid in client.list_models():
            print(mid)
    elif "--choose" in sys.argv:
        catalog = load_catalog(refresh="--refresh" in sys.argv)
        print("text:", pick_text_model(catalog))
        print("vision:", pick_vision_model(catalog))
    elif "--test" in sys.argv:
        catalog = load_catalog()
        client.usage.prices = prices_by_model(catalog)
        model = pick_text_model(catalog)
        try:
            client.chat([{"role": "user", "content": "Reply with OK"}],
                        model=model, max_tokens=5)
            print("test: success")
        except QuotaExhausted as exc:
            print("test: quota -", exc)
        except LLMError as exc:
            print("test: HTTP", exc.status)
