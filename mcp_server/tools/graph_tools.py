"""
GraphRAG-lite MCP tools (see app/graph/): entity-anchored "what relates to X" search, and
corpus-wide topic overviews from the community summaries built by
POST /api/v2/documents/graph/rebuild.

Neither tool calls an LLM. survey_topic returns the most relevant pre-built community
summaries as passages and leaves synthesis to the answerer, so a tool call has a bounded
cost (embedding comparisons only) and can't outlive the client's timeout.
"""
import json
from typing import Optional

from app.database.connection import SessionLocal
from app.database.models import CommunitySummary
from app.graph.global_search import rank_communities_by_relevance
from app.graph.local_search import local_search


def tool_find_related(entity: str, top_k: int = 5) -> str:
    db = SessionLocal()
    try:
        results = local_search(db, entity, top_k=top_k)
        return json.dumps(results, default=str)
    finally:
        db.close()


def tool_survey_topic(topic: str, max_communities: int = 5) -> str:
    db = SessionLocal()
    try:
        communities = db.query(CommunitySummary).all()
        if not communities:
            return json.dumps({
                "error": "No knowledge graph has been built for this corpus yet, so there are no "
                         "topic overviews. Use search_documentation instead.",
                "passages": [],
            })
        ranked = rank_communities_by_relevance(topic, communities)[:max(1, min(max_communities, 8))]
        passages = [{
            "chunk_id": f"community:{c.community_id}",
            "title": "Corpus topic overview",
            "section": f"Topic cluster #{c.community_id} ({c.chunk_count} passages)",
            "content": c.summary_text,
        } for c in ranked]
        return json.dumps({"passages": passages})
    finally:
        db.close()
