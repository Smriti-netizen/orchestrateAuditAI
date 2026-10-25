"""Create code.zip for submission (python zipfile, forward slashes)."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
ZIP_PATH = ROOT / "code.zip"

# Exclude patterns
EXCLUDE = {
    "__pycache__",
    "*.pyc",
    ".env",
    ".env.*",
    "cache",
    "usage_report.md",  # optional report
}

def should_exclude(path):
    """Check if a path should be excluded from the zip."""
    for part in path.parts:
        if part in EXCLUDE or any(part.endswith(ext) for ext in [".pyc"]):
            return True
    return False

def make_zip():
    """Create code.zip with forward slashes."""
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in CODE_DIR.rglob("*"):
            if file_path.is_file() and not should_exclude(file_path):
                arcname = file_path.relative_to(ROOT).as_posix()
                zf.write(file_path, arcname)

    print(f"Created {ZIP_PATH} with {len(zf.namelist())} files")

if __name__ == "__main__":
    make_zip()
