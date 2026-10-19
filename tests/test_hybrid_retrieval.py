from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.reranker import rerank


def test_reciprocal_rank_fusion():
    dense_res = [
        {"chunk_id": 101, "title": "Doc A", "source": "a.md", "similarity": 0.9},
        {"chunk_id": 102, "title": "Doc B", "source": "b.md", "similarity": 0.8}
    ]
    bm25_res = [
        {"chunk_id": 102, "title": "Doc B", "source": "b.md", "similarity": 5.4},
        {"chunk_id": 103, "title": "Doc C", "source": "c.md", "similarity": 3.2}
    ]

    fused = reciprocal_rank_fusion(dense_res, bm25_res, top_k=3, rrf_k=60)
    assert len(fused) == 3
    # 102 appeared in both lists, so it should have highest fused score
    assert fused[0]["chunk_id"] == 102
    assert fused[0]["dense_rank"] == 2
    assert fused[0]["bm25_rank"] == 1


def test_reranker():
    candidates = [
        {"chunk_id": 1, "content": "Kubernetes pods manage container lifecycles.", "similarity": 0.5},
        {"chunk_id": 2, "content": "Docker compose runs multi-container apps.", "similarity": 0.4}
    ]
    reranked = rerank("What is a Kubernetes pod?", candidates, top_k=2)
    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == 1
    assert "reranker_score" in reranked[0]
