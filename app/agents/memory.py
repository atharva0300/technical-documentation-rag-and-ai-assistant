import logging
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.database.connection import SessionLocal
from app.database.models import Message
from app.config import settings

logger = logging.getLogger(__name__)


class MemoryLoader:
    """
    Loads conversation history from DB and performs contextual query rewriting.
    """

    @staticmethod
    def load_history(conversation_id: str, limit: int = settings.MEMORY_TURNS, db: Optional[Session] = None) -> List[Dict[str, str]]:
        if not conversation_id:
            return []

        own_session = False
        if db is None:
            db = SessionLocal()
            own_session = True

        try:
            db_messages = (
                db.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            db_messages.reverse()
            return [{"role": msg.role, "content": msg.content} for msg in db_messages]
        except Exception:
            return []
        finally:
            if own_session:
                db.close()

    @staticmethod
    def rewrite_query(query: str, history: List[Dict[str, str]]) -> str:
        """
        Decides whether `query` is a follow-up that needs the previous turn's
        context folded in, and if so, does it -- using embedding similarity
        to the previous turn instead of a fixed phrase list
        (`"what about"`, `"how about"`, ...) or a word-count heuristic. A
        phrase list can only ever cover the specific ways someone thought to
        phrase a follow-up ("what about X") and will always miss others
        ("same question but for Y", "does that apply to Z too", a bare "and
        for staging?") -- a continuous similarity signal generalizes across
        phrasing the same way the semantic router (see
        `app.retrieval.semantic_router`) does for intent classification.

        Signal used: cosine similarity between this query's embedding and the
        previous user turn's embedding. A short, high-similarity-to-the-prior-
        turn query is treated as an incomplete follow-up and gets the prior
        turn's content appended as context; a long or low-similarity query is
        left alone (it's either self-contained or an unrelated new topic).
        """
        if not history:
            return query

        clean_query = query.strip()
        if not clean_query:
            return query

        last_user_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
        if not last_user_msg:
            return query

        try:
            from app.retrieval.semantic_router import embed_query
            import numpy as np

            query_vec = embed_query(clean_query)
            prior_vec = embed_query(last_user_msg)
            similarity = float(np.dot(query_vec, prior_vec))
        except Exception as e:
            logger.warning(f"Embedding-based follow-up detection unavailable ({e}); leaving query unrewritten.")
            return query

        # A short query that's still reasonably close in meaning to the prior
        # turn is almost always an incomplete follow-up ("what types are
        # available?" right after a question about a specific Kubernetes
        # resource only makes sense in that context). A short but SEMANTICALLY
        # UNRELATED query (low similarity) is a fresh topic, not a follow-up,
        # and is left alone -- this is exactly the generalization a keyword
        # list can't give you, since keyword lists have no notion of "related
        # to the previous turn," only "contains this exact phrase."
        is_short = len(clean_query.split()) <= 6
        is_related = similarity >= 0.35

        if is_short and is_related:
            return f"{clean_query} (Context: regarding {last_user_msg})"

        return query
