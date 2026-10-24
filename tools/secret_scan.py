"""Scan for first 8 chars of LLM_API_KEY from .env; print counts only."""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
env = REPO / ".env"
if not env.is_file():
    print("ERROR: .env missing")
    raise SystemExit(1)
text = env.read_text(encoding="utf-8", errors="replace")
m = re.search(r"LLM_API_KEY=(.{8})", text)
if not m:
    print("ERROR: LLM_API_KEY not found in .env")
    raise SystemExit(1)
prefix = m.group(1)

roots = [
    REPO / "log.txt",
    REPO / "README.md",
    REPO / "code",
    REPO / "code" / "evaluation",
    REPO / "code" / "media_extractions",
]
file_hits = 0
files_with = 0
for root in roots:
    paths = [root] if root.is_file() else root.rglob("*")
    for p in paths:
        if not p.is_file():
            continue
        if p.suffix in {".pyc"} or "__pycache__" in p.parts:
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = body.count(prefix)
        if n:
            files_with += 1
            file_hits += n

proc = subprocess.run(
    ["git", "log", "-p", "--all"],
    cwd=str(REPO),
    capture_output=True,
    text=True,
    errors="replace",
)
git_hits = proc.stdout.count(prefix) if proc.returncode == 0 else -1

print(f"prefix_length=8")
print(f"tracked_text_files_with_matches={files_with}")
print(f"tracked_text_total_match_count={file_hits}")
print(f"git_log_p_all_match_count={git_hits}")
