"""Runtime paths and constants for the Ghost Data Still Exists deletion-audit agent."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "dataset"
EVIDENCE_DIR = DATA_DIR / "evidence"
OUTPUT_PATH = REPO_ROOT / "output.csv"

DATASET_DIRNAME = "dataset"

DELETION_REQUESTS = DATA_DIR / "deletion_requests.csv"
SAMPLE_REQUESTS = DATA_DIR / "sample_deletion_requests.csv"
SAMPLE_OUTPUT = DATA_DIR / "sample_output.csv"
IDENTITIES = DATA_DIR / "user_identities.csv"
AUDIT_LOG = DATA_DIR / "user_audit_log.csv"
ACTIVITY = DATA_DIR / "system_activity.csv"
RULES = DATA_DIR / "deletion_rules.csv"
EVIDENCE_MANIFEST = DATA_DIR / "evidence_files.csv"

ENV_LLM_PROVIDER = "LLM_PROVIDER"
ENV_LLM_MODEL = "LLM_MODEL"
ENV_LLM_API_KEY = "LLM_API_KEY"

DEFAULT_LLM_MODEL = "gemini-2.0-flash"
