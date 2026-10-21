from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import JSONB
from app.database.models import Chunk, Document
from app.embeddings.model import generate_embedding

def search_dense(
    db: Session, 
    query: str, 
    top_k: int = 20, 
    threshold: float = 0.0,
    filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Dense vector search using pgvector cosine distance.
    Supports metadata filters (source, document_id, section).
    """
    query_embedding = generate_embedding(query)

    stmt = (
        select(Chunk, Document.title, Document.source, Document.document_id, Chunk.embedding.cosine_distance(query_embedding).label("distance"))
        .join(Document, Chunk.document_id == Document.id)
    )

    if filters:
        if "source" in filters and filters["source"]:
            stmt = stmt.where(Document.source == filters["source"])
        if "document_id" in filters and filters["document_id"]:
            stmt = stmt.where(Document.document_id == filters["document_id"])
        if filters.get("has_code"):
            stmt = stmt.where(cast(Chunk.metadata_json, JSONB)["has_code"].as_boolean().is_(True))
        if filters.get("language"):
            stmt = stmt.where(cast(Chunk.metadata_json, JSONB)["code_languages"].contains([filters["language"].lower()]))
        if "section" in filters and filters["section"]:
            stmt = stmt.where(Chunk.section.ilike(f"%{filters['section']}%"))
        if "updated_after" in filters and filters["updated_after"]:
            if isinstance(filters["updated_after"], str):
                dt = datetime.fromisoformat(filters["updated_after"])
            else:
                dt = filters["updated_after"]
            stmt = stmt.where(Document.updated_at >= dt)

    stmt = stmt.order_by(Chunk.embedding.cosine_distance(query_embedding)).limit(top_k)

    results = db.execute(stmt).all()
    
    retrieved_chunks = []
    for rank, (chunk, doc_title, doc_source, doc_id, distance) in enumerate(results, start=1):
        similarity = max(0.0, 1.0 - float(distance))
        
        if threshold > 0 and similarity < threshold:
            continue
            
        retrieved_chunks.append({
            "chunk_id": chunk.id,
            "document_id": doc_id,
            "title": doc_title,
            "source": doc_source,
            "section": chunk.section,
            "content": chunk.content,
            "similarity": similarity,
            "dense_score": similarity,
            "dense_rank": rank
        })

    return retrieved_chunks
