"""Maintainer tool: extract screenshot facts once and cache them by file hash.

    python code/extract_media.py [--limit N] [--force] [--pdf]

Not needed for a normal run: main.py reads the cache written here.
"""
import argparse
import sys
from pathlib import Path

from audit import config
from audit.evidence import load_manifest
from audit.llm import LLMClient, LLMError, QuotaExhausted, load_catalog, pick_vision_model
from audit import media


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--pdf", action="store_true", help="also normalise PDFs locally")
    args = ap.parse_args(argv)

    catalog = load_catalog()
    model = pick_vision_model(catalog)
    print("vision model:", model)
    client = LLMClient(model=model)

    files = [f for f in load_manifest() if f.path.lower().endswith(".png")]
    if args.pdf:
        for ef in load_manifest():
            if ef.path.lower().endswith(".pdf"):
                path = config.DATA_DIR / ef.path
                facts = media.pdf_to_facts(media.pdf_text(path), ef.file_id, ef.captured_at)
                print(f"{ef.file_id}: pdf facts={len(facts)} "
                      f"resources={[f.resource_id for f in facts][:6]}")
    if args.limit:
        files = files[:args.limit]

    done = 0
    for ef in files:
        path = config.DATA_DIR / ef.path
        cached = media.cached_image_doc(ef.file_id, path)
        if cached and not args.force:
            doc = cached["extracted"]
            print(f"{ef.file_id}: cached screen={doc.get('screen')!r} "
                  f"subject={(doc.get('subject') or {}).get('user_id')} "
                  f"rows={len(doc.get('rows') or [])}")
            continue
        try:
            doc = media.extract_image(client, model, path)
        except QuotaExhausted as exc:
            print("STOP:", exc)
            break
        except (LLMError, ValueError) as exc:
            print(f"{ef.file_id}: FAILED {exc}")
            continue
        media.save_image_doc(ef.file_id, path, ef.captured_at, doc)
        done += 1
        print(f"{ef.file_id}: screen={doc.get('screen')!r} "
              f"subject={(doc.get('subject') or {}).get('user_id')} "
              f"rows={[(r.get('system'), r.get('resource_id'), r.get('state')) for r in doc.get('rows') or []]}")
    print("new extractions:", done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
