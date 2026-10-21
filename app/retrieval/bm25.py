import re
import json
import logging
import hashlib
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from rank_bm25 import BM25Okapi
from app.database.models import Chunk, Document

logger = logging.getLogger(__name__)

_bm25_cache = {
    "bm25_index": None,
    "chunks": [],
    "hash_version": ""
}

def _tokenize(text: str) -> List[str]:
    """
    Technical tokenizer preserving CLI flags (--memory), version strings (v1.2.3),
    dotted names, hyphenated identifiers, and camelCase terms.
    """
    if not text:
        return []
    
    text_lower = text.lower()
    # Extract technical tokens preserving flags, versions, dotted/hyphenated identifiers, and alphanumeric words
    tokens = re.findall(r'--[a-z0-9_-]+|v?\d+\.\d+(?:\.\d+)?|[a-z0-9]+(?:[-_.][a-z0-9]+)*', text_lower)
    return tokens

def invalidate_bm25_cache():
    """Call when documents or chunks are ingested/deleted."""
    global _bm25_cache
    _bm25_cache = {
        "bm25_index": None,
        "chunks": [],
        "hash_version": ""
    }

def get_bm25_index(db: Session):
    """Retrieve or build the cached BM25 index with content-version validation."""
    global _bm25_cache

    # Version on chunk COUNT + each document's own content_hash + updated_at,
    # not a sample of the first 10 chunk ids. The old id-sample hash missed:
    # (a) a chunk's content edited in place (same id, same count -- never
    #     invalidated), and (b) a delete+add pair that nets the same count
    # and happens to leave the sampled ids unchanged. content_hash/updated_at
    # change on every real content edit (see app.ingestion.pipeline), so this
    # catches both.
    rows = (
        db.query(Document.id, Document.content_hash, Document.updated_at, Chunk.id)
        .join(Chunk, Chunk.document_id == Document.id)
        .all()
    )
    if not rows:
        _bm25_cache = {"bm25_index": None, "chunks": [], "hash_version": ""}
        return None, []

    doc_versions = sorted({
        f"{doc_id}:{content_hash}:{updated_at.isoformat() if updated_at else ''}"
        for doc_id, content_hash, updated_at, _chunk_id in rows
    })
    version_str = f"{len(rows)}_" + "_".join(doc_versions)
    current_hash = hashlib.md5(version_str.encode()).hexdigest()

    if _bm25_cache["bm25_index"] is not None and _bm25_cache["hash_version"] == current_hash:
        return _bm25_cache["bm25_index"], _bm25_cache["chunks"]

    stmt = select(Chunk, Document.title, Document.source, Document.document_id, Document.updated_at).join(Document, Chunk.document_id == Document.id)
    results = db.execute(stmt).all()

    if not results:
        _bm25_cache = {"bm25_index": None, "chunks": [], "hash_version": ""}
        return None, []

    chunks = []
    tokenized_corpus = []
    for chunk, doc_title, doc_source, doc_id, doc_updated_at in results:
        try:
            meta = json.loads(chunk.metadata_json) if chunk.metadata_json else {}
        except (ValueError, TypeError):
            meta = {}
        chunks.append({
            "chunk_id": chunk.id,
            "document_id": doc_id,
            "title": doc_title,
            "source": doc_source,
            "section": chunk.section,
            "content": chunk.content,
            "updated_at": doc_updated_at,
            "has_code": bool(meta.get("has_code")),
            "code_languages": meta.get("code_languages") or [],
        })
        tokenized_corpus.append(_tokenize(chunk.content))

    bm25 = BM25Okapi(tokenized_corpus)
    _bm25_cache = {
        "bm25_index": bm25,
        "chunks": chunks,
        "hash_version": current_hash
    }
    return bm25, chunks

def search_bm25(
    db: Session, 
    query: str, 
    top_k: int = 20,
    filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Search for relevant chunks using technical BM25 lexical retrieval.
    """
    bm25, chunks = get_bm25_index(db)
    if not bm25 or not chunks:
        return []

    tokenized_query = _tokenize(query)
    if not tokenized_query:
        return []

    doc_scores = bm25.get_scores(tokenized_query)

    scored_chunks = []
    for idx, (chunk_info, score) in enumerate(zip(chunks, doc_scores)):
        if score <= 0:
            continue
            
        # Apply filters symmetrically
        if filters:
            if "source" in filters and filters["source"] and chunk_info["source"] != filters["source"]:
                continue
            if "document_id" in filters and filters["document_id"] and chunk_info["document_id"] != filters["document_id"]:
                continue
            if filters.get("has_code") and not chunk_info.get("has_code"):
                continue
            if filters.get("language") and filters["language"].lower() not in chunk_info.get("code_languages", []):
                continue
            if "section" in filters and filters["section"] and (not chunk_info["section"] or filters["section"].lower() not in chunk_info["section"].lower()):
                continue
            if "updated_after" in filters and filters["updated_after"]:
                from datetime import datetime
                dt = datetime.fromisoformat(filters["updated_after"]) if isinstance(filters["updated_after"], str) else filters["updated_after"]
                if chunk_info.get("updated_at") and chunk_info["updated_at"] < dt:
                    continue

        chunk_copy = chunk_info.copy()
        chunk_copy["similarity"] = float(score)
        chunk_copy["bm25_score"] = float(score)
        scored_chunks.append(chunk_copy)

    scored_chunks.sort(key=lambda x: x["similarity"], reverse=True)

    retrieved_chunks = []
    for rank, chunk in enumerate(scored_chunks[:top_k], start=1):
        chunk["bm25_rank"] = rank
        retrieved_chunks.append(chunk)

    return retrieved_chunks
