import os
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.retrieval.dense import search_dense
from app.retrieval.bm25 import search_bm25
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.reranker import rerank
from app.retrieval.query_analysis import compute_lexical_specificity, resolve_hybrid_weights

logger = logging.getLogger(__name__)

DEFAULT_HYBRID_TOP_K = int(os.getenv("HYBRID_TOP_K", "20"))
DEFAULT_RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))


def _fuse(db: Session, query: str, dense_candidates, bm25_candidates, top_k: int):
    """
    Fuses dense + BM25 candidates, biasing the fusion toward whichever channel
    the query's own corpus-relative lexical-specificity signal favors (see
    app.retrieval.query_analysis) instead of a fixed, unweighted blend.
    Replaces the old behavior where the planner tried to force "exact_term"
    queries down a hardcoded-tool path; here, the same effect (favor exact
    lexical matches when the query genuinely contains rare, corpus-specific
    terms) is achieved generically, at the retrieval-fusion layer, for any
    corpus, with no hardcoded vocabulary anywhere.
    """
    specificity = 0.0
    try:
        specificity = compute_lexical_specificity(db, query)
    except Exception as e:
        logger.debug(f"Lexical specificity computation skipped: {e}")

    weights = resolve_hybrid_weights(specificity)
    if weights:
        return reciprocal_rank_fusion(dense_candidates, bm25_candidates, top_k=top_k, **weights)
    return reciprocal_rank_fusion(dense_candidates, bm25_candidates, top_k=top_k)


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
    - 'hybrid': Dense + BM25 combined via lexical-specificity-weighted RRF
    - 'hybrid_rerank': Dense + BM25 + weighted RRF -> Cross-Encoder reranking
    """
    strategy = strategy.lower().strip()

    if strategy == "dense":
        return search_dense(db, query, top_k=top_k, filters=filters)

    elif strategy == "bm25":
        return search_bm25(db, query, top_k=top_k, filters=filters)

    elif strategy == "hybrid":
        dense_candidates = search_dense(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        bm25_candidates = search_bm25(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        return _fuse(db, query, dense_candidates, bm25_candidates, top_k=top_k)

    elif strategy in ("hybrid_rerank", "hybrid+reranker", "rerank"):
        dense_candidates = search_dense(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        bm25_candidates = search_bm25(db, query, top_k=DEFAULT_HYBRID_TOP_K, filters=filters)
        hybrid_candidates = _fuse(db, query, dense_candidates, bm25_candidates, top_k=DEFAULT_HYBRID_TOP_K)
        return rerank(query, hybrid_candidates, top_k=top_k)

    else:
        # Default fallback to hybrid_rerank
        return perform_search(db, query, top_k=top_k, strategy="hybrid_rerank", filters=filters)
