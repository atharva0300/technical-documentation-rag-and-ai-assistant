import pytest
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.reranker import rerank
from app.retrieval.bm25 import _tokenize

def test_rrf_combines_dense_and_bm25_candidates():
    dense_list = [
        {"chunk_id": 1, "content": "HNSW index configuration", "score": 0.9},
        {"chunk_id": 2, "content": "IVFFlat index parameters", "score": 0.8}
    ]
    bm25_list = [
        {"chunk_id": 2, "content": "IVFFlat index parameters", "similarity": 5.2},
        {"chunk_id": 3, "content": "PostgreSQL memory tuning", "similarity": 4.1}
    ]

    fused = reciprocal_rank_fusion(dense_list, bm25_list, top_k=3)
    assert len(fused) == 3
    # Chunk 2 appears in both lists, so its fused RRF score should rank highest
    assert fused[0]["chunk_id"] == 2

def test_technical_tokenizer_preserves_code_identifiers_and_paths():
    text = "Use --memory-limit in /etc/postgresql/postgresql.conf for v1.2.3 and restartPolicy"
    tokens = _tokenize(text)
    assert "--memory-limit" in tokens
    assert "v1.2.3" in tokens
    assert "restartpolicy" in tokens or "restart-policy" in tokens or "restart" in tokens

def test_reranker_reorders_chunks_by_query_relevance():
    query = "PostgreSQL memory tuning"
    candidates = [
        {"chunk_id": 1, "content": "Kubernetes pod replica configuration", "title": "k8s.md"},
        {"chunk_id": 2, "content": "PostgreSQL shared_buffers and memory tuning parameters", "title": "postgres.md"}
    ]

    # Reranking should place chunk 2 (relevant to memory tuning) at rank 1
    reranked = rerank(query, candidates, top_k=2)
    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == 2
