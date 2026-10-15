import os
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.retrieval.dense import search_dense
from app.retrieval.bm25 import search_bm25
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.reranker import rerank

DEFAULT_HYBRID_TOP_K = int(os.getenv("HYBRID_TOP_K", "20"))
DEFAULT_RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))

def perform_search(
    db: Session, 
    query: str, 
    top_k: int = 5, 
    strategy: str = "hybrid_rerank",
    filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Unified search entrypoint supporting configurable search strategies:
    - 'dense': Dense vector retrieval only
    - 'bm25': BM25 lexical retrieval only
    - 'hybrid': Dense + BM25 combined via Reciprocal Rank Fusion (RRF)
    - 'hybrid_rerank': Dense + BM25 + RRF -> Cross-Encoder reranking
    """
    strategy = strategy.lower().strip()

    if strategy == "dense":
        return search_dense(db, query, top_k=top_k, filters=filters)
    
    elif strategy == "bm25":
        return search_bm25(db, query, top_k=top_k, filters=filters)

    elif strategy == "hybrid":
        dense_candidates = search_dense(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        bm25_candidates = search_bm25(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        return reciprocal_rank_fusion(dense_candidates, bm25_candidates, top_k=top_k)

    elif strategy in ("hybrid_rerank", "hybrid+reranker", "rerank"):
        dense_candidates = search_dense(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        bm25_candidates = search_bm25(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        hybrid_candidates = reciprocal_rank_fusion(dense_candidates, bm25_candidates, top_k=DEFAULT_HYBRID_TOP_K)
        return rerank(query, hybrid_candidates, top_k=top_k)

    else:
        # Default fallback to hybrid_rerank
        return perform_search(db, query, top_k=top_k, strategy="hybrid_rerank", filters=filters)
