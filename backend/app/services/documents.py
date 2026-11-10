"""Document ingestion, search, analysis, sourced report generation, and export."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import secrets
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape  # noqa: S406

from docx import Document as DocxDocument
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm.base import structured_call
from app.agents.llm.factory import get_llm
from app.agents.safety import SYSTEM_POLICY, detect_injection, wrap_untrusted
from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.database.base import utcnow
from app.integrations.factory import is_demo
from app.models import Document
from app.services.activity import log_activity
from app.utils.dates import extract_all_dates, get_zone
from app.utils.text import score_documents, truncate

logger = logging.getLogger("autopilot.documents")

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".csv": "text/csv",
}
MAX_EXTRACTED_CHARS = 400_000
MAX_LLM_CHARS = 80_000
MAX_PDF_PAGES = 2000
MAX_DOCX_UNZIPPED_BYTES = 200 * 1024 * 1024


def _safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]", "_", name).strip(". ")
    return name[:200] or "document"


def validate_upload(filename: str, data: bytes) -> tuple[str, str]:
    name = _safe_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in MIME_BY_EXT:
        raise ValidationFailed(f"Unsupported file type. Allowed: {', '.join(sorted(MIME_BY_EXT))}.")
    if not data:
        raise ValidationFailed("The file is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"The file exceeds the {settings.max_upload_mb} MB limit.", code="file_too_large")
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValidationFailed("This file is not a valid PDF.")
    if ext == ".docx" and not data.startswith(b"PK\x03\x04"):
        raise ValidationFailed("This file is not a valid DOCX.")
    if ext in (".txt", ".csv") and b"\x00" in data[:8192]:
        raise ValidationFailed("This file appears to be binary, not text.")
    return name, ext


def extract_text(ext: str, data: bytes) -> str:
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    elif ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNZIPPED_BYTES:
                raise ValueError("This DOCX expands to an unsafe size.")
        doc = DocxDocument(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        parts += [" | ".join(c.text.strip() for c in row.cells) for t in doc.tables for row in t.rows]
        text = "\n".join(parts)
    else:
        decoded = data.decode("utf-8-sig", errors="replace")
        if ext == ".csv":
            text = "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(decoded)))
        else:
            text = decoded
    return text.strip()[:MAX_EXTRACTED_CHARS]


def create_from_bytes(db: Session, user_id: uuid.UUID, filename: str, data: bytes, *, kind: str = "upload", drive_file_id: str | None = None) -> Document:
    name, ext = validate_upload(filename, data)
    directory = Path(settings.upload_dir) / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{secrets.token_hex(16)}{ext}"  # the client's filename never reaches the filesystem
    path.write_bytes(data)
    doc = Document(user_id=user_id, title=Path(name).stem[:255], filename=name, mime_type=MIME_BY_EXT[ext], size_bytes=len(data), storage_path=str(path), kind=kind, drive_file_id=drive_file_id)
    try:
        doc.content = extract_text(ext, data)
        if not doc.content:
            doc.status, doc.error = "failed", "No text could be extracted (the file may be a scanned image)."
    except Exception as exc:  # noqa: BLE001 - parsers raise many types on hostile files; none should crash an upload
        logger.info("document_extraction_failed", extra={"reason": exc.__class__.__name__})
        doc.status, doc.error = "failed", f"Could not read this file: {exc}" if isinstance(exc, ValueError) else "Could not read this file."
    doc.injection_flags = detect_injection(doc.content)
    db.add(doc)
    db.flush()
    log_activity(db, user_id, "document_uploaded", f"Uploaded {name}", entity_type="document", entity_id=doc.id, status="success" if doc.status == "ready" else "failure")
    return doc


def visible_query(db: Session, user_id: uuid.UUID):
    stmt = select(Document).where(Document.user_id == user_id)
    return stmt if is_demo(db, user_id) else stmt.where(Document.is_demo.is_(False))


def get_owned(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user_id))
    if doc is None:
        raise NotFound("Document not found.")
    return doc


def search(db: Session, user_id: uuid.UUID, query: str, limit: int = 5, include_reports: bool = False) -> list[tuple[Document, float]]:
    stmt = visible_query(db, user_id).where(Document.status == "ready")
    if not include_reports:
        stmt = stmt.where(Document.kind != "report")
    docs = db.scalars(stmt.limit(500)).all()
    scores = score_documents(query, [(str(d.id), f"{d.title} {d.title} {d.content}") for d in docs])
    ranked = sorted(((d, scores[str(d.id)]) for d in docs if str(d.id) in scores), key=lambda p: -p[1])
    return ranked[:limit]


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}, "topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "key_points", "topics"],
}
ANALYST_SYSTEM = f"You analyse documents for the user. Use only information present in the document.\n\n{SYSTEM_POLICY}"


def _llm_doc(doc: Document) -> dict:
    return {"id": str(doc.id), "title": doc.title, "content": doc.content[:MAX_LLM_CHARS]}


def analyze(db: Session, user_id: uuid.UUID, doc: Document, timezone: str) -> Document:
    if doc.status != "ready":
        raise ValidationFailed("This document has no readable text to analyse.")
    llm = get_llm()
    result = structured_call(llm, purpose="document.analyze", system=ANALYST_SYSTEM, user=wrap_untrusted(_llm_doc(doc), secrets.token_hex(8)), schema=ANALYSIS_SCHEMA)
    today = utcnow().astimezone(get_zone(timezone)).date()
    doc.analysis = {**result, "dates": extract_all_dates(doc.content, today)[:25], "analyzed_at": utcnow().isoformat(), "simulated_ai": llm.simulated}
    log_activity(db, user_id, "document_analyzed", f"Analysed {doc.title}", actor="document", entity_type="document", entity_id=doc.id, simulated=llm.simulated)
    db.commit()
    return doc


def compare(db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict:
    if len(set(ids)) < 2:
        raise ValidationFailed("Choose at least two different documents.")
    docs = [get_owned(db, user_id, i) for i in ids[:4]]
    if any(d.status != "ready" for d in docs):
        raise ValidationFailed("All documents must contain readable text.")
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "similarities": {"type": "array", "items": {"type": "string"}}, "differences": {"type": "array", "items": {"type": "string"}}},
        "required": ["summary", "similarities", "differences"],
    }
    llm = get_llm()
    result = structured_call(llm, purpose="document.compare", system=ANALYST_SYSTEM, user=wrap_untrusted({"documents": [_llm_doc(d) for d in docs]}, secrets.token_hex(8)), schema=schema)
    log_activity(db, user_id, "documents_compared", f"Compared {', '.join(d.title for d in docs)}", actor="document", simulated=llm.simulated)
    db.commit()
    return {**result, "documents": [{"id": str(d.id), "title": d.title} for d in docs], "simulated_ai": llm.simulated}


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "object", "properties": {"statement": {"type": "string"}, "source": {"type": "string", "description": "Source label such as S1"}}, "required": ["statement", "source"]}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "executive_summary", "facts", "suggestions"],
}


def generate_report(db: Session, user_id: uuid.UUID, ids: list[uuid.UUID], topic: str) -> Document:
    if not ids:
        raise ValidationFailed("Select at least one source document.")
    sources = [get_owned(db, user_id, i) for i in ids[:6]]
    if any(d.status != "ready" or d.kind == "report" for d in sources):
        raise ValidationFailed("Sources must be readable uploaded documents.")
    system = (
        "Write a structured report using ONLY the provided documents. Every fact must cite its source label (S1, S2...). "
        "Anything not stated in the sources belongs in suggestions, never in facts.\n\n" + SYSTEM_POLICY
    )
    labelled = {"documents": [{"label": f"S{n}", **_llm_doc(d)} for n, d in enumerate(sources, 1)]}
    task = json.dumps({"topic": topic})
    llm = get_llm()
    result = structured_call(llm, purpose="report.generate", system=system, user=f"<task>{task}</task>\n{wrap_untrusted(labelled, secrets.token_hex(8))}", schema=REPORT_SCHEMA, max_tokens=12000)
    valid = {f"S{n}" for n in range(1, len(sources) + 1)}
    facts = [f for f in result["facts"] if f["source"] in valid]  # uncited claims are dropped rather than shown as verified
    lines = [f"# {result['title']}", "", result["executive_summary"], "", "## Verified facts", "_Stated in the source documents._", ""]
    lines += [f"- {f['statement']} [{f['source']}]" for f in facts] or ["_No supporting facts were found in the sources._"]
    lines += ["", "## AI suggestions", "_Not stated in the sources. Review before relying on these._", ""]
    lines += [f"- {s}" for s in result["suggestions"]] or ["_None._"]
    lines += ["", "## Sources"] + [f"- [S{n}] {d.title}" for n, d in enumerate(sources, 1)]
    return save_report(db, user_id, truncate(result["title"], 200), "\n".join(lines), sources, actor="document", simulated=llm.simulated)


def save_report(db: Session, user_id: uuid.UUID, title: str, content: str, sources: list[Document], *, actor: str, simulated: bool = False) -> Document:
    report = Document(
        user_id=user_id, title=title[:255], filename=f"{re.sub(r'[^\w-]+', '-', title)[:80].strip('-') or 'report'}.md", mime_type="text/markdown",
        size_bytes=len(content.encode()), kind="report", content=content, sources=[{"id": str(d.id), "title": d.title} for d in sources], is_demo=is_demo(db, user_id),
    )
    db.add(report)
    db.flush()
    log_activity(db, user_id, "report_generated", f"Generated report: {report.title}", actor=actor, entity_type="document", entity_id=report.id, simulated=simulated)
    return report


def _pdf_inline(text: str) -> str:
    escaped = escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", escaped))


def export_pdf(doc: Document) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    story = []
    for line in doc.content.splitlines():
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
        elif stripped.startswith("# "):
            story.append(Paragraph(_pdf_inline(stripped[2:]), styles["Title"]))
        elif stripped.startswith("## "):
            story.append(Paragraph(_pdf_inline(stripped[3:]), styles["Heading2"]))
        elif stripped.startswith("- "):
            story.append(Paragraph(_pdf_inline(stripped[2:]), styles["BodyText"], bulletText="•"))
        else:
            story.append(Paragraph(_pdf_inline(stripped), styles["BodyText"]))
    SimpleDocTemplate(buffer, pagesize=A4, title=doc.title).build(story)
    return buffer.getvalue()


def export_docx(doc: Document) -> bytes:
    output = DocxDocument()
    for line in doc.content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            output.add_heading(stripped[2:], level=0)
        elif stripped.startswith("## "):
            output.add_heading(stripped[3:], level=1)
        elif stripped.startswith("- "):
            output.add_paragraph(stripped[2:].replace("**", ""), style="List Bullet")
        else:
            output.add_paragraph(stripped.replace("**", ""))
    buffer = io.BytesIO()
    output.save(buffer)
    return buffer.getvalue()
