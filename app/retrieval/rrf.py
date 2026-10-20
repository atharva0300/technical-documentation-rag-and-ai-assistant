import os
from typing import List, Dict, Any, Optional

DEFAULT_RRF_K = int(os.getenv("RRF_K", "60"))


def reciprocal_rank_fusion(
    dense_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    top_k: int = 20,
    rrf_k: int = DEFAULT_RRF_K,
    dense_weight: float = 1.0,
    bm25_weight: float = 1.0,
    graph_results: Optional[List[Dict[str, Any]]] = None,
    graph_weight: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    Combines ranked result lists using weighted Reciprocal Rank Fusion (RRF).

    RRF_score(d) = sum( weight_i / (rrf_k + rank_i(d)) ) over every source that ranked d.

    `dense_weight`/`bm25_weight` let a caller bias the fusion toward whichever
    channel the query's own lexical-specificity signal favors (see
    `app.retrieval.query_analysis`) instead of a fixed 50/50 blend — passing
    no weights (the default) reproduces the original unweighted RRF exactly.

    `graph_results` is an optional third ranked list (e.g. a GraphRAG local-
    search entity-traversal ranking — see `app.graph.local_search`), fused in
    the same way: RRF generalizes to any number of ranked sources, it isn't
    specific to "exactly dense + BM25."
    """
    scores: Dict[int, float] = {}
    chunk_map: Dict[int, Dict[str, Any]] = {}
    dense_ranks: Dict[int, int] = {}
    bm25_ranks: Dict[int, int] = {}
    graph_ranks: Dict[int, int] = {}

    def _accumulate(results: List[Dict[str, Any]], weight: float, rank_map: Dict[int, int]):
        for rank, item in enumerate(results, start=1):
            cid = item["chunk_id"]
            rank_map[cid] = rank
            scores[cid] = scores.get(cid, 0.0) + (weight / (rrf_k + rank))
            if cid not in chunk_map:
                chunk_map[cid] = item.copy()
            else:
                # Chunk already surfaced by another source — carry forward this
                # source's own score fields (dense_score/bm25_score/graph_score)
                # onto the merged record instead of discarding them.
                for score_field in ("dense_score", "bm25_score", "graph_score"):
                    if score_field in item:
                        chunk_map[cid][score_field] = item[score_field]

    _accumulate(dense_results, dense_weight, dense_ranks)
    _accumulate(bm25_results, bm25_weight, bm25_ranks)
    if graph_results:
        _accumulate(graph_results, graph_weight, graph_ranks)

    # Sort chunks by fused RRF score descending
    sorted_chunk_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    hybrid_results = []
    for cid in sorted_chunk_ids[:top_k]:
        item = chunk_map[cid].copy()
        item["dense_rank"] = dense_ranks.get(cid, None)
        item["bm25_rank"] = bm25_ranks.get(cid, None)
        if graph_results:
            item["graph_rank"] = graph_ranks.get(cid, None)
        item["rrf_score"] = round(scores[cid], 6)
        # Preserve similarity field as rrf_score for uniform API contract
        item["similarity"] = round(scores[cid], 6)
        hybrid_results.append(item)

    return hybrid_results
