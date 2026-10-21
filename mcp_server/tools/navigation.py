"""
Corpus-navigation MCP tools: what's indexed, how a document is organised, and the full
text around a passage. Each returns JSON with a "passages" list (what the agent hands to
the answerer as citable evidence) plus structured fields for other MCP clients.
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from app.database.connection import SessionLocal
from app.database.models import Chunk, Document

# Section labels are "<heading> · p. <page>" (see app/ingestion/chunker.py).
_PAGE_SUFFIX = re.compile(r'\s·\sp\.\s(\d+)$')
# In stored document text: "## Page N" markers from PDF extraction, and markdown headings
# (numbered PDF headings are promoted to "### ..." by app/ingestion/loader.py).
_PAGE_MARKER = re.compile(r'^##\s+Page\s+(\d+)\s*$')
_HEADING_LINE = re.compile(r'^#{1,6}\s+(.+?)\s*$')

MAX_SECTION_CHUNKS = 12
MAX_SECTION_CHARS = 8000
MAX_TOC_ENTRIES = 80


def _heading_and_page(section: Optional[str]):
    if not section:
        return None, None
    match = _PAGE_SUFFIX.search(section)
    if match:
        return section[:match.start()], int(match.group(1))
    return section, None


def _resolve_document(db, document_id: Optional[str]):
    """Returns (document, None) or (None, error). With a single indexed document, that
    document is used whenever document_id is missing or unrecognised (callers often pass
    a chapter number or a made-up id). Otherwise errors list the valid ids so the caller
    can retry correctly."""
    if document_id:
        doc = db.query(Document).filter(Document.document_id == document_id).first()
        if doc is not None:
            return doc, None
    docs = db.query(Document).order_by(Document.title).all()
    if len(docs) == 1:
        return docs[0], None
    valid = ", ".join(f"{d.document_id} ({d.title})" for d in docs) or "none indexed"
    if document_id:
        return None, f"No document with id '{document_id}'. Valid document_id values: {valid}."
    return None, f"Several documents are indexed; pass a document_id. Valid document_id values: {valid}."


def _passage(chunk: Chunk, doc: Document) -> Dict[str, Any]:
    return {
        "chunk_id": chunk.id,
        "document_id": doc.document_id,
        "title": doc.title,
        "source": doc.source,
        "section": chunk.section,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
    }


def _section_chunks(db, doc: Document, heading: str) -> List[Tuple[Chunk, str]]:
    """The chunks covering one section, in reading order: from the chunk where the heading
    line appears (or the first chunk labelled with it) through the following chunks that
    still belong to that section."""
    needle = heading.lower()
    all_chunks = db.query(Chunk).filter(Chunk.document_id == doc.id).order_by(Chunk.chunk_index).all()

    start = None
    for i, chunk in enumerate(all_chunks):
        label, _ = _heading_and_page(chunk.section)
        opens_here = any(
            (m := _HEADING_LINE.match(line.strip())) and m.group(1).lower().startswith(needle)
            for line in chunk.content.splitlines()
        )
        if opens_here or (label and label.lower().startswith(needle)):
            start = i
            break
    if start is None:
        return []

    target = next(
        (m.group(1) for line in all_chunks[start].content.splitlines()
         if (m := _HEADING_LINE.match(line.strip())) and m.group(1).lower().startswith(needle)),
        _heading_and_page(all_chunks[start].section)[0],
    )

    # Walk forward until another heading opens; that chunk still holds the tail of this
    # section (chunks are labelled by the first heading they open, not the one they continue).
    selected = []
    for chunk in all_chunks[start:]:
        text = chunk.content
        if not selected:
            text = _from_heading(text, target)
        next_heading_at = _next_heading_offset(text, target)
        if next_heading_at is not None:
            text = text[:next_heading_at].rstrip()
            if text:
                selected.append((chunk, text))
            break
        selected.append((chunk, text))
    return selected


def _from_heading(text: str, heading: str) -> str:
    """Drops whatever precedes the section's heading line in its first chunk."""
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _HEADING_LINE.match(line.strip())
        if match and match.group(1) == heading:
            return text[offset:]
        offset += len(line)
    return text


def _next_heading_offset(text: str, current: str):
    """Character offset of the first heading line other than `current`, or None."""
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _HEADING_LINE.match(line.strip())
        if match and match.group(1) != current and not _PAGE_MARKER.match(line.strip()):
            return offset
        offset += len(line)
    return None


def tool_list_documents() -> str:
    db = SessionLocal()
    try:
        rows = (
            db.query(Document, func.count(Chunk.id))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.id)
            .order_by(Document.title)
            .all()
        )
        documents = [{
            "document_id": doc.document_id,
            "title": doc.title,
            "chunks": count,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        } for doc, count in rows]
        lines = [f"- {d['title']} (document_id {d['document_id']}, {d['chunks']} passages, "
                 f"updated {(d['updated_at'] or 'unknown')[:10]})" for d in documents]
        content = (f"{len(documents)} document(s) are indexed:\n" + "\n".join(lines)) if documents \
            else "No documents are indexed yet."
        return json.dumps({
            "documents": documents,
            "passages": [{"chunk_id": "inventory", "title": "Indexed documents",
                          "section": "Document inventory", "content": content}],
        })
    finally:
        db.close()


def tool_get_table_of_contents(document_id: Optional[str] = None, under: Optional[str] = None) -> str:
    db = SessionLocal()
    try:
        doc, error = _resolve_document(db, document_id)
        if error:
            return json.dumps({"error": error})

        # Read headings from the document text rather than chunk labels: a chunk is labelled
        # by the section it starts in, so a short section that starts mid-chunk has no label.
        entries, seen, page = [], set(), None
        for line in (doc.content or "").splitlines():
            line = line.strip()
            page_match = _PAGE_MARKER.match(line)
            if page_match:
                page = int(page_match.group(1))
                continue
            heading_match = _HEADING_LINE.match(line)
            if heading_match:
                heading = heading_match.group(1)
                if heading not in seen:
                    seen.add(heading)
                    entries.append({"heading": heading, "page": page})

        if under:
            needle = under.strip().lower().rstrip(".")
            if re.fullmatch(r'\d+(?:\.\d+)*', needle):
                # A section number: that section and its subsections ("9" -> 9, 9.1, 9.8.2;
                # not 19.2 or 3.9).
                entries = [e for e in entries
                           if re.match(rf'{re.escape(needle)}(?:[.\s]|$)', e["heading"].lower())]
            else:
                entries = [e for e in entries if needle in e["heading"].lower()]

        truncated = len(entries) > MAX_TOC_ENTRIES
        shown = entries[:MAX_TOC_ENTRIES]
        lines = [f"{e['heading']} (p. {e['page']})" if e["page"] else e["heading"] for e in shown]
        scope = f" matching '{under}'" if under else ""
        content = f"Sections of {doc.title}{scope}:\n" + "\n".join(lines) if lines \
            else f"No sections of {doc.title}{scope} were found."
        if truncated:
            content += (f"\n… {len(entries) - MAX_TOC_ENTRIES} more. Pass `under` (a section number "
                        "like '9' or '9.8', or a word in the heading) to narrow it.")
        return json.dumps({
            "document_id": doc.document_id,
            "title": doc.title,
            "entries": shown,
            "truncated": truncated,
            "passages": [{"chunk_id": f"toc:{doc.document_id}:{under or ''}", "document_id": doc.document_id,
                          "title": doc.title, "section": "Table of contents", "content": content}],
        })
    finally:
        db.close()


def _render_passages(selected: List[Tuple[Chunk, str]], doc: Document) -> str:
    passages, total = [], 0
    for chunk, text in selected[:MAX_SECTION_CHUNKS]:
        total += len(text)
        if passages and total > MAX_SECTION_CHARS:
            break
        passages.append({**_passage(chunk, doc), "content": text})
    return json.dumps({"passages": passages, "truncated": len(passages) < len(selected)})


def tool_get_section(section: str, document_id: Optional[str] = None) -> str:
    db = SessionLocal()
    try:
        if document_id or db.query(Document).count() == 1:
            doc, error = _resolve_document(db, document_id)
            if error:
                return json.dumps({"error": error})
            candidates = [doc]
        else:
            candidates = db.query(Document).order_by(Document.title).all()

        for doc in candidates:
            selected = _section_chunks(db, doc, section.strip())
            if selected:
                return _render_passages(selected, doc)
        return json.dumps({"error": f"No section heading matches '{section}'. Pass the heading as "
                                    "'<number> <title>' from a search result or get_table_of_contents."})
    finally:
        db.close()


def tool_expand_passage(chunk_id: int, window: int = 2) -> str:
    db = SessionLocal()
    try:
        anchor = db.query(Chunk).filter(Chunk.id == chunk_id).first()
        if anchor is None:
            return json.dumps({"error": f"No passage with chunk_id {chunk_id}. Use a chunk_id from a search result."})
        doc = anchor.document
        window = max(0, min(window, 5))
        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc.id,
                    Chunk.chunk_index.between(anchor.chunk_index - window, anchor.chunk_index + window))
            .order_by(Chunk.chunk_index)
            .all()
        )
        return _render_passages([(chunk, chunk.content) for chunk in chunks], doc)
    finally:
        db.close()
