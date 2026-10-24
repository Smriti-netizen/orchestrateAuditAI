"""Evidence manifest loader."""
from dataclasses import dataclass
from pathlib import Path

from audit.config import EVIDENCE_MANIFEST


@dataclass
class EvidenceFile:
    file_id: str
    path: str          # path relative to dataset/
    origin: str
    captured_at: str


def load_manifest(path=None) -> list[EvidenceFile]:
    path = Path(path) if path else EVIDENCE_MANIFEST
    out = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        import csv as _csv
        for row in _csv.DictReader(fh):
            if not (row.get("file_id") or "").strip():
                continue
            out.append(EvidenceFile(
                file_id=row["file_id"].strip(),
                path=row["file_path"].strip(),
                origin=(row.get("origin") or "").strip(),
                captured_at=(row.get("captured_at") or "").strip(),
            ))
    return out
