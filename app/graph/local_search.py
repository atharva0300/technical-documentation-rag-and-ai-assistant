"""
GraphRAG-lite "local search": entity-anchored graph traversal for relational
queries ("what depends on X", "what breaks if I change X"), fused with the
existing dense + BM25 retrieval via the same `reciprocal_rank_fusion` used
everywhere else -- RRF generalizes to any number of ranked sources, so a
graph-traversal ranking is just a third list, not a new fusion mechanism.
"""
import json
import logging
from typing import Dict, Any, List

from sqlalchemy.orm import Session

from app.database.models import Chunk
from app.graph.entity_extraction import extract_entities
from app.retrieval.dense import search_dense
from app.retrieval.bm25 import search_bm25
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.reranker import rerank

logger = logging.getLogger(__name__)

MAX_HOPS = 2


def _find_anchor_chunks(db: Session, query_entities: List[str]) -> List[int]:
    if not query_entities:
        return []
    lowered = {e.lower() for e in query_entities}
    anchors = []
    for chunk in db.query(Chunk.id, Chunk.entities_json).filter(Chunk.entities_json.isnot(None)).all():
        try:
            entities = {e.lower() for e in json.loads(chunk.entities_json)}
        except Exception:
            continue
        if lowered & entities:
            anchors.append(chunk.id)
    return anchors


def _build_entity_adjacency(db: Session) -> Dict[int, set]:
    """Builds chunk_id -> {neighbor chunk_ids sharing an entity}, reusing the
    same cached entities_json the community builder writes (see
    app/graph/community_builder.py) rather than recomputing a full graph."""
    rows = db.query(Chunk.id, Chunk.entities_json).filter(Chunk.entities_json.isnot(None)).all()
    entity_to_chunks: Dict[str, List[int]] = {}
    chunk_entities: Dict[int, set] = {}
    for chunk_id, entities_json in rows:
        try:
            entities = set(e.lower() for e in json.loads(entities_json))
        except Exception:
            entities = set()
        chunk_entities[chunk_id] = entities
        for e in entities:
            entity_to_chunks.setdefault(e, []).append(chunk_id)

    adjacency: Dict[int, set] = {cid: set() for cid in chunk_entities}
    for e, chunk_ids in entity_to_chunks.items():
        if len(chunk_ids) < 2 or len(chunk_ids) > 50:
            continue
        for a in chunk_ids:
            for b in chunk_ids:
                if a != b:
                    adjacency[a].add(b)
    return adjacency


def _bfs_rank(adjacency: Dict[int, set], anchors: List[int], max_hops: int = MAX_HOPS) -> List[int]:
    """Returns chunk ids ordered by BFS hop distance from any anchor (closer first)."""
    visited = {a: 0 for a in anchors}
    frontier = list(anchors)
    hop = 0
    while frontier and hop < max_hops:
        next_frontier = []
        for node in frontier:
            for neighbor in adjacency.get(node, ()):
                if neighbor not in visited:
                    visited[neighbor] = hop + 1
                    next_frontier.append(neighbor)
        frontier = next_frontier
        hop += 1
    return [cid for cid, _ in sorted(visited.items(), key=lambda kv: kv[1])]


def local_search(db: Session, query: str, top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Entity-anchored BFS over the chunk-entity graph, fused with ordinary
    dense + BM25 retrieval via RRF. Falls back to plain dense+BM25 (no graph
    boost) if no entities in the query match anything in the graph -- this
    is a relational-intent *bias* on top of standard retrieval, not a
    replacement for it, since GraphRAG's own local search mode also combines
    graph traversal with vector similarity over the resulting text units
    rather than using the graph alone.
    """
    query_entities = extract_entities(query)
    anchors = _find_anchor_chunks(db, query_entities)

    dense_candidates = search_dense(db, query, top_k=20, filters=filters)
    bm25_candidates = search_bm25(db, query, top_k=20, filters=filters)

    graph_results = None
    if anchors:
        adjacency = _build_entity_adjacency(db)
        ranked_ids = _bfs_rank(adjacency, anchors)
        if ranked_ids:
            chunk_rows = {c.id: c for c in db.query(Chunk).filter(Chunk.id.in_(ranked_ids[:50])).all()}
            graph_results = []
            for rank, cid in enumerate(ranked_ids, start=1):
                chunk = chunk_rows.get(cid)
                if not chunk:
                    continue
                graph_results.append({
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "section": chunk.section,
                    "source": chunk.source,
                    "content": chunk.content,
                    "graph_score": 1.0 / rank,
                })

    fused = reciprocal_rank_fusion(
        dense_candidates, bm25_candidates, top_k=20,
        graph_results=graph_results, graph_weight=1.2 if graph_results else 1.0,
    )
    return rerank(query, fused, top_k=top_k)
