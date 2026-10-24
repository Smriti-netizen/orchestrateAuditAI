"""Media evidence parsers: PDF text extraction first, VLM/OCR fallback (cached by hash later)."""


def parse_pdf(path, file_id, captured_at):
    errors = []
    facts = []
    try:
        import pypdf  # optional dependency
        reader = pypdf.PdfReader(str(path))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        if not text or not text.strip():
            return [], [f"{file_id}: PDF has no extractable text layer; defer to VLM"]
        return [], [f"{file_id}: PDF text extracted ({len(text)} chars); needs fact normalisation"]
    except Exception as exc:
        return [], [f"{file_id}: PDF parse failed: {exc}"]


def parse_image(path, file_id, captured_at):
    return [], [f"{file_id}: image requires VLM extraction (deferred to milestone 4)"]
