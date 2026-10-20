"""
Builds the chunk-entity graph and detects communities (Louvain) over it.

This is a corpus-level batch operation -- it runs once per `sync_directory`
call (or on demand via the `/api/v2/documents/graph/rebuild` endpoint), not
per single-document ingest, because community structure is a property of
the WHOLE corpus, not any one document. This mirrors GraphRAG's own
indexing-cost trade-off: building this is more expensive than plain
chunk-and-embed, so it's done as a deliberate batch step, not on every
upload.
"""
import json
import logging
from collections import defaultdict
from typing import Dict, Any

from sqlalchemy.orm import Session

from app.database.models import Chunk, CommunitySummary
from app.graph.entity_extraction import extract_entities
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHUNKS = 8  # cap how many chunk excerpts feed one community's LLM summary


def rebuild_knowledge_graph(db: Session) -> Dict[str, Any]:
    """
    1. Extracts entities for every chunk (cached to `Chunk.entities_json` so
       repeated rebuilds don't re-run extraction on unchanged chunks).
    2. Builds a networkx graph: one node per chunk, edges weighted by shared
       entity count between chunk pairs.
    3. Runs Louvain community detection (networkx's built-in implementation --
       a close, widely-used sibling of GraphRAG's Leiden) and assigns
       `Chunk.community_id`.
    4. Generates one summary per community (LLM-assisted, with a
       deterministic fallback when the LLM is unavailable) into
       `CommunitySummary`, which `app.graph.global_search` later maps/reduces
       over for "exhaustive" queries.
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    chunks = db.query(Chunk).all()
    if not chunks:
        return {"status": "skipped", "reason": "no chunks in corpus", "communities": 0}

    chunk_entities: Dict[int, list] = {}
    for chunk in chunks:
        if chunk.entities_json:
            try:
                chunk_entities[chunk.id] = json.loads(chunk.entities_json)
                continue
            except Exception:
                pass
        entities = extract_entities(chunk.content)
        chunk_entities[chunk.id] = entities
        chunk.entities_json = json.dumps(entities)

    db.commit()

    graph = nx.Graph()
    for chunk in chunks:
        graph.add_node(chunk.id)

    entity_to_chunks: Dict[str, list] = defaultdict(list)
    for chunk_id, entities in chunk_entities.items():
        for entity in entities:
            entity_to_chunks[entity.lower()].append(chunk_id)

    edge_weights: Dict[tuple, int] = defaultdict(int)
    for entity, chunk_ids in entity_to_chunks.items():
        if len(chunk_ids) < 2 or len(chunk_ids) > 50:
            # Skip entities shared by too many chunks (near-stopword-like,
            # not a useful graph edge) or appearing only once (no edge to form).
            continue
        unique_ids = sorted(set(chunk_ids))
        for i in range(len(unique_ids)):
            for j in range(i + 1, len(unique_ids)):
                edge_weights[(unique_ids[i], unique_ids[j])] += 1

    for (a, b), weight in edge_weights.items():
        graph.add_edge(a, b, weight=weight)

    if graph.number_of_edges() == 0:
        logger.info("Knowledge graph has no edges (no shared entities across chunks) -- skipping community detection.")
        return {"status": "skipped", "reason": "no shared entities found", "communities": 0}

    communities = louvain_communities(graph, weight="weight", seed=42)

    chunk_by_id = {c.id: c for c in chunks}
    for community_id, member_ids in enumerate(communities):
        for chunk_id in member_ids:
            chunk_by_id[chunk_id].community_id = community_id

    db.commit()

    # Clear stale community summaries from a previous rebuild before writing new ones.
    db.query(CommunitySummary).delete()
    db.commit()

    summaries_written = 0
    for community_id, member_ids in enumerate(communities):
        member_chunks = [chunk_by_id[cid] for cid in member_ids]
        entity_count = len({e for cid in member_ids for e in chunk_entities.get(cid, [])})
        summary_text = _summarize_community(member_chunks)

        db.add(CommunitySummary(
            community_id=community_id,
            summary_text=summary_text,
            chunk_count=len(member_chunks),
            entity_count=entity_count,
        ))
        summaries_written += 1

    db.commit()

    return {
        "status": "success",
        "communities": summaries_written,
        "chunks_processed": len(chunks),
        "edges": graph.number_of_edges(),
    }


def _summarize_community(member_chunks) -> str:
    excerpt_chunks = member_chunks[:MAX_SUMMARY_CHUNKS]
    joined = "\n\n---\n\n".join(c.content[:800] for c in excerpt_chunks)

    try:
        return llm_gateway.generate(
            prompt=f"Summarize the key topics and facts covered by these related documentation excerpts:\n\n{joined}",
            system=(
                "You are summarizing a cluster of related technical documentation chunks for a "
                "knowledge graph. Write a concise (3-6 sentence) summary covering what this group "
                "of chunks is collectively about, so the summary alone can answer questions about "
                "topics scattered across all of them."
            ),
        )
    except OllamaUnavailableError:
        # Deterministic fallback: a plain concatenation of section headers,
        # keeping the pipeline functional (if less informative) without a
        # live LLM, consistent with the fallback pattern already used in
        # app.agents.answerer and app.retrieval.reranker.
        sections = sorted({c.section for c in excerpt_chunks if c.section})
        return "Community covering sections: " + ", ".join(sections) if sections else \
            f"Community of {len(member_chunks)} related chunks (LLM summarization unavailable)."
