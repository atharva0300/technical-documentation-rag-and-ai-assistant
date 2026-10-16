import json
from typing import Dict, Any, Optional
from app.database.connection import SessionLocal
from app.retrieval.search import perform_search


def tool_search_documentation(query: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None) -> str:
    """
    Search indexed technical documentation using hybrid dense + BM25 retrieval, fused with RRF
    and cross-encoder reranking ("hybrid_rerank" — the only strategy exposed publicly; dense/bm25/
    hybrid remain internal `app.retrieval.search` functions used only by the eval scripts).
    """
    db = SessionLocal()
    try:
        results = perform_search(db, query, top_k=top_k, strategy="hybrid_rerank", filters=filters)
        return json.dumps(results, indent=2)
    finally:
        db.close()
