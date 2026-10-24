"""Media evidence: local PDF text extraction + cached VLM extraction for screenshots.

Images are read once per content hash through the Groq vision model and cached under
code/media_extractions/. That directory is shipped inside code.zip (it is not under
code/cache/), so the runtime never needs the network and a graded run is deterministic.
"""
import base64
import hashlib
import json
from pathlib import Path

from audit.config import REPO_ROOT
from audit.evidence.facts import Fact

MEDIA_CACHE = REPO_ROOT / "code" / "media_extractions"
PROMPT_VERSION = 3  # bump when VISION_PROMPT changes; forces --force re-extraction

VISION_PROMPT = """You extract structured records from a business application screenshot.

Return ONLY a JSON object:
{
  "screen": "short human description of the screen",
  "subject": {"user_id": "user id shown or empty", "emails": [], "phones": [], "resource_ids": []},
  "topology": "primary|regional_replica|reporting_replica|failover_replica|n/a",
  "rows": [
    {"system": "Account|Search|Analytics|Support|Export|Backup|Integration|Security|Identity|Marketing|Orders|Finance",
     "resource_id": "exact record/reference id displayed",
     "category": "USER_PROFILE|SEARCH_INDEX_ENTRY|ANALYTICS_PROFILE|SUPPORT_MESSAGE|USER_EXPORT|BACKUP_ARCHIVE|DATA_FLOW|SECURITY_AUDIT_LOG|ACCOUNT_TOMBSTONE|MARKETING_PROFILE|ORDER_RECORD|FINANCE_RECORD",
     "state": "PRESENT|ABSENT|DELETED|ACTIVE|REDACTED|ANONYMIZED|UNKNOWN",
     "identifiers": {"user_id": "", "emails": [], "phones": []},
     "details": "status, retention date, enabled flag or other short facts shown for this row"}
  ]
}

Rules:
- Transcribe identifiers exactly as displayed, never reformat them.
- Every row gets the person identifiers printed on that row (customer, owner, email,
  phone). On a list screen rows concern different people, so never copy the header
  person into every row.
- Emit a row only for a record the screen visibly lists. Never invent ids or rows.
- resource_id must be the system record id, never a person id. Values like user_20 are
  person ids: put them in subject.user_id, never in resource_id.
- System record ids look like account_5762, search_428, analytics_7438, marketing_001,
  support_9538, export_1261, backup_9287, data_flow_1121, audit_record_003, tombstone_12.
- If a detail page shows the subject line as "user_20 <middot> analytics_7438 <middot> email",
  emit one row with resource_id analytics_7438 and keep the email in subject.emails.
- For the Account system use the account_* id from the header, not the user id.
- Ticket/order references are copied as displayed (CS-5034, ORD-20265034, FIN-7742).
- Use "" or [] when a value is not visible.
- Output the JSON object and nothing else."""

SYSTEM_MAP = {
    "account": "account_service", "account platform": "account_service",
    "customer operations": "account_service", "customers": "account_service",
    "search": "search_service", "search index": "search_service",
    "analytics": "analytics_platform", "analytics platform": "analytics_platform",
    "support": "support_platform", "support platform": "support_platform",
    "export": "export_service", "exports": "export_service", "customer export": "export_service",
    "backup": "backup_service", "backups": "backup_service", "restore": "backup_service",
    "integration": "integration_service", "integrations": "integration_service",
    "connector": "integration_service", "connectors": "integration_service",
    "data flow": "integration_service",
    "security": "security_service", "audit": "security_service", "security audit": "security_service",
    "identity": "identity_service", "tombstone": "identity_service",
    "marketing": "marketing_platform", "promotions": "marketing_platform",
    "orders": "finance_service", "finance": "finance_service",
    "object storage": "object_storage",
}

CATEGORY_MAP = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "backup_service": "BACKUP_ARCHIVE",
    "integration_service": "DATA_FLOW",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "marketing_platform": "MARKETING_PROFILE",
    "finance_service": "FINANCE_RECORD",
    "object_storage": "OBJECT_STORE_OBJECT",
}

ABSENT_STATES = {"ABSENT", "DELETED", "MISSING", "NOT_FOUND", "REMOVED", "NONE"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


RESOURCE_PREFIXES = (
    ("data_flow_", "integration_service"), ("audit_record_", "security_service"),
    ("tombstone_", "identity_service"), ("account_", "account_service"),
    ("search_", "search_service"), ("analytics_", "analytics_platform"),
    ("support_", "support_platform"), ("export_", "export_service"),
    ("backup_", "backup_service"), ("marketing_", "marketing_platform"),
)


def system_id_for(name, resource_id=""):
    """Resource-id prefixes beat screen labels (e.g. audit_record_* on an account page)."""
    rid = (resource_id or "").strip().lower()
    for prefix, sid in RESOURCE_PREFIXES:
        if rid.startswith(prefix):
            return sid
    if rid.startswith(("ord-", "fin-")):
        return "finance_service"
    if rid.startswith("cs-"):
        return "support_platform"
    key = (name or "").strip().lower()
    if key in SYSTEM_MAP:
        return SYSTEM_MAP[key]
    for token, sid in SYSTEM_MAP.items():
        if token and token in key:
            return sid
    return "unknown"


def extract_image(client, model, path):
    """One vision call per image (the client caches by model+prompt+image bytes)."""
    raw = Path(path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    text = client.chat(messages, model=model, max_tokens=800, temperature=0.0)
    return parse_json_loose(text)


def parse_json_loose(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def doc_to_facts(doc, file_id, captured_at):
    """Normalise a VLM document into Facts (same shape as every other parser)."""
    facts = []
    seen = set()
    subject = doc.get("subject") or {}
    idents = {}
    if subject.get("user_id"):
        idents["user_ref"] = str(subject["user_id"]).strip()
    emails = subject.get("emails") or []
    if emails:
        idents["email"] = str(emails[0]).strip()
    phones = subject.get("phones") or []
    if phones:
        idents["phone"] = str(phones[0]).strip()

    def emit(system_id, resource_id, category, state, details, row_idents=None):
        key = (system_id, resource_id)
        if not resource_id or key in seen:
            return
        seen.add(key)
        row_idents = row_idents or {}
        merged = dict(idents)
        if row_idents.get("user_id"):
            merged["user_ref"] = str(row_idents["user_id"]).strip()
        if row_idents.get("emails"):
            merged["email"] = str(row_idents["emails"][0]).strip()
        if row_idents.get("phones"):
            merged["phone"] = str(row_idents["phones"][0]).strip()
        facts.append(Fact(
            file_id=file_id,
            captured_at=captured_at,
            system_id=system_id,
            resource_id=resource_id,
            category=category or CATEGORY_MAP.get(system_id, "UNKNOWN"),
            presence="ABSENT" if state in ABSENT_STATES else "PRESENT",
            identifiers=merged,
            extra={"screen": doc.get("screen", ""), "state": state, "details": details},
            topology=doc.get("topology") or "n/a",
            covers=False,  # a filtered UI screen never proves absence
        ))

    for row in doc.get("rows") or []:
        resource_id = str(row.get("resource_id") or "").strip()
        if not resource_id or resource_id.lower().startswith("user_"):
            continue  # person id, not a system record
        if any(ch.isspace() for ch in resource_id):
            continue  # a display name, not a resource id
        system_id = system_id_for(row.get("system"), resource_id)
        emit(system_id, resource_id, row.get("category"),
             str(row.get("state") or "").strip().upper(), row.get("details", ""),
             row.get("identifiers") or {})

    # Detail pages often carry the record id only on the subject line.
    for resource_id in subject.get("resource_ids") or []:
        resource_id = str(resource_id).strip()
        if not resource_id or resource_id.lower().startswith("user_"):
            continue
        system_id = system_id_for("", resource_id)
        emit(system_id, resource_id, None, "PRESENT", "subject header")
    return facts


def cached_image_doc(file_id, path):
    p = MEDIA_CACHE / f"{file_id}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("sha256") != sha256_file(path):
        return None
    return data


def save_image_doc(file_id, path, captured_at, doc):
    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    p = MEDIA_CACHE / f"{file_id}.json"
    p.write_text(json.dumps({"file_id": file_id, "path": str(path), "captured_at": captured_at,
                             "sha256": sha256_file(path), "prompt_version": PROMPT_VERSION,
                             "extracted": doc},
                            indent=2, ensure_ascii=False), encoding="utf-8")
    return p


# ---------- PDFs (local text layer, no LLM) ----------

def pdf_text(path):
    import pypdf
    reader = pypdf.PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def pdf_to_facts(text, file_id, captured_at):
    """Heuristic normaliser for the label/value PDF layouts in this dataset."""
    import re
    facts = []
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    user_ids = sorted(set(re.findall(r"\buser_\d+\b", text)))
    resources = sorted(set(re.findall(
        r"\b(?:support|audit_record|account|search|backup|export|tombstone)_\d+\b", text)))
    retain = sorted(set(re.findall(r"Retain until (\d{2} \w{3} \d{4})", text)))
    if not (emails or user_ids or resources):
        return facts

    # Retention register: one fact per disputed order row.
    rows = re.findall(
        r"(ORD-\d+)\s*\|\s*(user_\d+)\s*\|\s*([^|]+?)\s*\|\s*([\d]{2} \w{3} \d{4})\s*\|"
        r"\s*([A-Z]{3} [\d.,]+)\s*\|\s*Retain until ([\d]{2} \w{3} \d{4})", text)
    for order_id, uid, reason, _date, amount, retain_until in rows:
        facts.append(Fact(
            file_id=file_id, captured_at=captured_at, system_id="finance_service",
            resource_id=order_id, category="FINANCE_RECORD", presence="PRESENT",
            identifiers={"user_ref": uid},
            extra={"reason": reason.strip(), "amount": amount,
                   "retention_until": retain_until},
            topology="n/a", covers=False))
    if rows:
        return facts

    # Single-subject documents (statement, support attachment, audit extract).
    resource_id = resources[0] if resources else ""
    system_id = "unknown"
    if resource_id.startswith("support"):
        system_id = "support_platform"
    elif resource_id.startswith("audit_record"):
        system_id = "security_service"
    idents = {}
    if user_ids:
        idents["user_ref"] = user_ids[0]
    if emails:
        idents["email"] = emails[0]
    if resource_id:
        # A support PDF is an attachment captured from the ticket, so policy
        # requires deleting the file rather than redacting a conversation row.
        category = "SUPPORT_ATTACHMENT" if resource_id.startswith("support") \
            else CATEGORY_MAP.get(system_id, "UNKNOWN")
        facts.append(Fact(
            file_id=file_id, captured_at=captured_at, system_id=system_id,
            resource_id=resource_id, category=category,
            presence="PRESENT", identifiers=idents,
            extra={"retention_until": retain[0] if retain else "", "record_exists": True},
            topology="n/a", covers=False))
        return facts

    # No system resource id: emit one document fact per referenced order/reference.
    order_ids = sorted(set(re.findall(r"\bORD-\d+\b", text)))
    for order_id in order_ids:
        facts.append(Fact(
            file_id=file_id, captured_at=captured_at, system_id="finance_service",
            resource_id=order_id, category="FINANCE_RECORD", presence="PRESENT",
            identifiers=idents, extra={"retention_until": retain[0] if retain else ""},
            topology="n/a", covers=False))
    return facts
