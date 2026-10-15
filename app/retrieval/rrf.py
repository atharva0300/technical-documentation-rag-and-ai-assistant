import os
from typing import List, Dict, Any

DEFAULT_RRF_K = int(os.getenv("RRF_K", "60"))

def reciprocal_rank_fusion(
    dense_results: List[Dict[str, Any]], 
    bm25_results: List[Dict[str, Any]], 
    top_k: int = 20,
    rrf_k: int = DEFAULT_RRF_K
) -> List[Dict[str, Any]]:
    """
    Combines Dense and BM25 search results using Reciprocal Rank Fusion (RRF).
    
    RRF_score(d) = sum( 1 / (rrf_k + rank(d)) ) for each retrieval model.
    """
    scores: Dict[int, float] = {}
    chunk_map: Dict[int, Dict[str, Any]] = {}
    dense_ranks: Dict[int, int] = {}
    bm25_ranks: Dict[int, int] = {}

    # Process Dense results
    for rank, item in enumerate(dense_results, start=1):
        cid = item["chunk_id"]
        dense_ranks[cid] = rank
        scores[cid] = scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))
        if cid not in chunk_map:
            chunk_map[cid] = item.copy()
        else:
            chunk_map[cid]["dense_score"] = item.get("dense_score")

    # Process BM25 results
    for rank, item in enumerate(bm25_results, start=1):
        cid = item["chunk_id"]
        bm25_ranks[cid] = rank
        scores[cid] = scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))
        if cid not in chunk_map:
            chunk_map[cid] = item.copy()
        else:
            chunk_map[cid]["bm25_score"] = item.get("bm25_score")

    # Sort chunks by fused RRF score descending
    sorted_chunk_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    hybrid_results = []
    for cid in sorted_chunk_ids[:top_k]:
        item = chunk_map[cid].copy()
        item["dense_rank"] = dense_ranks.get(cid, None)
        item["bm25_rank"] = bm25_ranks.get(cid, None)
        item["rrf_score"] = round(scores[cid], 6)
        # Preserve similarity field as rrf_score for uniform API contract
        item["similarity"] = round(scores[cid], 6)
        hybrid_results.append(item)

    return hybrid_results
