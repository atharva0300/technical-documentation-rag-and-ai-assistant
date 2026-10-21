import logging
from typing import List, Dict, Any
from sentence_transformers import CrossEncoder

from app.config import settings

logger = logging.getLogger(__name__)

RERANKER_MODEL_NAME = settings.RERANKER_MODEL_NAME

_reranker_model = None

def get_reranker_model() -> CrossEncoder:
    """Singleton pattern to load the cross-encoder reranker model on CPU once."""
    global _reranker_model
    if _reranker_model is None:
        logger.info(f"Loading CrossEncoder model: {RERANKER_MODEL_NAME}")
        _reranker_model = CrossEncoder(RERANKER_MODEL_NAME, device="cpu")
    return _reranker_model

def rerank(query: str, candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Reranks candidate chunks for a query using a Cross-Encoder model.
    Receives (query, chunk_content) pairs and returns top_k highest scored candidates.
    """
    if not candidates:
        return []

    model = get_reranker_model()
    
    pairs = [(query, cand["content"]) for cand in candidates]
    scores = model.predict(pairs)

    # Attach reranker scores
    for cand, score in zip(candidates, scores):
        cand["reranker_score"] = float(score)

    # Sort descending by reranker score
    reranked_candidates = sorted(candidates, key=lambda x: x["reranker_score"], reverse=True)

    top_results = []
    for rank, cand in enumerate(reranked_candidates[:top_k], start=1):
        cand_copy = cand.copy()
        cand_copy["reranker_rank"] = rank
        # Standardize similarity to reranker score
        cand_copy["similarity"] = float(cand["reranker_score"])
        top_results.append(cand_copy)

    return top_results
