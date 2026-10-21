import os
import json
import logging
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from app.config import settings
from app.database.models import Document, Chunk
from app.ingestion.loader import load_file
from app.ingestion.cleaner import clean_text
from app.ingestion.chunker import TokenAwareChunker
from app.embeddings.model import generate_embeddings_batch, EMBEDDING_MODEL_NAME
from app.retrieval.bm25 import invalidate_bm25_cache
from app.graph.community_builder import rebuild_knowledge_graph

logger = logging.getLogger(__name__)

_chunker = TokenAwareChunker()


def ingest_document(db: Session, file_path: str) -> Dict[str, Any]:
    """
    Idempotent document ingestion pipeline:
    1. Loads document & calculates SHA-256 content hash.
    2. Checks if document exists by source path.
       - If source path & same content_hash -> skips re-indexing.
       - If source path & different content_hash -> deletes old chunks atomically & updates.
       - If new -> creates document and chunks.
    3. Guarantees transaction safety via rollback on error.

    Chunking is token-aware (TokenAwareChunker), sized to the embedding model's
    actual 256-token sequence limit (settings.MAX_CHUNK_SIZE), rather than approximated in words.
    """
    # 1. Load document
    doc_data = load_file(file_path)
    source_path = doc_data["source"]
    content_hash = doc_data["content_hash"]

    chunking_config_str = f"TokenAwareChunker(max={_chunker.max_tokens},overlap={_chunker.overlap_tokens})"

    # 2. Check existing document by source path
    existing_doc = db.query(Document).filter(Document.source == source_path).first()

    if existing_doc:
        if existing_doc.content_hash == content_hash:
            return {
                "status": "skipped",
                "message": "Document content unchanged (SHA-256 match)",
                "document_id": existing_doc.document_id,
                "content_hash": content_hash,
                "chunks_created": len(existing_doc.chunks)
            }

        # Document updated -> Atomic update inside transaction
        try:
            db.query(Chunk).filter(Chunk.document_id == existing_doc.id).delete()

            cleaned_content = clean_text(doc_data["content"])
            chunks_data = _chunker.chunk_text(cleaned_content)

            existing_doc.title = doc_data["title"]
            existing_doc.content = doc_data["content"]
            existing_doc.content_hash = content_hash
            existing_doc.chunking_config = chunking_config_str
            existing_doc.embedding_model = EMBEDDING_MODEL_NAME

            embeddings = generate_embeddings_batch([c["content"] for c in chunks_data])
            for idx, (c_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                meta = {
                    "source": source_path,
                    "section": c_data["section"],
                    "chunk_index": idx,
                    "document_hash": content_hash,
                    "has_code": c_data.get("has_code", False),
                    "page": c_data.get("page")
                }
                new_chunk = Chunk(
                    document_id=existing_doc.id,
                    chunk_index=idx,
                    section=c_data["section"],
                    source=source_path,
                    content=c_data["content"],
                    embedding=embedding,
                    metadata_json=json.dumps(meta)
                )
                db.add(new_chunk)

            db.commit()
            db.refresh(existing_doc)
            invalidate_bm25_cache()

            return {
                "status": "updated",
                "message": "Document updated & re-indexed",
                "document_id": existing_doc.document_id,
                "content_hash": content_hash,
                "chunks_created": len(chunks_data)
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update document {source_path}: {e}")
            raise e

    # 3. New Document Insertion
    try:
        cleaned_content = clean_text(doc_data["content"])
        chunks_data = _chunker.chunk_text(cleaned_content)

        new_doc = Document(
            document_id=doc_data["document_id"],
            title=doc_data["title"],
            source=source_path,
            url=None,
            content=doc_data["content"],
            content_hash=content_hash,
            chunking_config=chunking_config_str,
            embedding_model=EMBEDDING_MODEL_NAME
        )
        db.add(new_doc)
        db.flush()  # Populate new_doc.id

        # Batched: encoding chunks one at a time left most of the CPU idle between
        # tiny forward passes and made large documents take many minutes.
        embeddings = generate_embeddings_batch([c["content"] for c in chunks_data])
        for idx, (c_data, embedding) in enumerate(zip(chunks_data, embeddings)):
            meta = {
                "source": source_path,
                "section": c_data["section"],
                "chunk_index": idx,
                "document_hash": content_hash,
                "has_code": c_data.get("has_code", False),
                "page": c_data.get("page")
            }
            new_chunk = Chunk(
                document_id=new_doc.id,
                chunk_index=idx,
                section=c_data["section"],
                source=source_path,
                content=c_data["content"],
                embedding=embedding,
                metadata_json=json.dumps(meta)
            )
            db.add(new_chunk)

        db.commit()
        invalidate_bm25_cache()

        return {
            "status": "success",
            "message": "Document ingested successfully",
            "document_id": new_doc.document_id,
            "content_hash": content_hash,
            "chunks_created": len(chunks_data)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to ingest document {source_path}: {e}")
        raise e


def sync_directory(db: Session, directory_path: str) -> Dict[str, Any]:
    """
    Synchronizes documents in the directory:
    - Ingests new or modified documents in directory.
    - Safely deletes documents from DB whose source path no longer exists on disk.
    """
    if not os.path.exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}")

    ingested = []
    skipped = []
    updated = []

    # Track existing files on disk
    disk_sources = set()
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.endswith((".md", ".txt")):
                full_path = os.path.abspath(os.path.join(root, file))
                disk_sources.add(full_path)
                res = ingest_document(db, full_path)
                if res["status"] == "success":
                    ingested.append(full_path)
                elif res["status"] == "updated":
                    updated.append(full_path)
                else:
                    skipped.append(full_path)

    # Detect deleted documents
    deleted_docs = []
    all_db_docs = db.query(Document).all()
    for doc in all_db_docs:
        if doc.source and doc.source.startswith(os.path.abspath(directory_path)):
            if doc.source not in disk_sources:
                deleted_docs.append(doc.source)
                db.delete(doc)

    if deleted_docs:
        db.commit()
        invalidate_bm25_cache()

    graph_result = {"status": "skipped", "reason": "no corpus changes"}
    if ingested or updated or deleted_docs:
        # Community detection is a corpus-level operation (see
        # app.graph.community_builder) -- rebuilt once per sync, not per
        # individual document, since community structure depends on the
        # whole corpus's entity graph, not any single file.
        try:
            graph_result = rebuild_knowledge_graph(db)
        except Exception as e:
            logger.warning(f"Knowledge graph rebuild failed (sync still succeeded): {e}")
            graph_result = {"status": "error", "error": str(e)}

    return {
        "status": "sync_complete",
        "ingested_count": len(ingested),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "deleted_count": len(deleted_docs),
        "deleted_sources": deleted_docs,
        "knowledge_graph": graph_result
    }
