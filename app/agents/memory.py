import logging
import re
from dataclasses import dataclass
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.database.connection import SessionLocal
from app.database.models import Message
from app.config import settings

logger = logging.getLogger(__name__)

_REFERRING_WORDS = re.compile(r"\b(it|its|that|this|these|those|them|they|the same|above|previous)\b", re.IGNORECASE)

CONTEXTUALIZE_SYSTEM_PROMPT = """You rewrite the user's latest question so it can be understood on its own.
Use the conversation to resolve references such as "it", "that", "this", "them" or "the
same" to the specific technology, class, feature or topic they refer to, and include any
context from earlier turns that the question depends on.
Do not answer the question. Do not add information that isn't in the conversation.
If the latest question is already self-contained, return it unchanged.
Reply with only the rewritten question."""

# Earlier assistant answers are trimmed: the question being rewritten only needs to know
# what was being discussed, not the full answer text.
_ANSWER_CONTEXT_CHARS = 400
_CONTEXT_TURNS = 4


@dataclass
class ContextualizedQuery:
    text: str
    method: str  # "none" (no history), "llm", or "heuristic" (LLM unavailable or unusable)
    detail: Optional[str] = None


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
        Fallback follow-up handling for when the LLM contextualizer (QueryContextualizer)
        can't run. Decides whether `query` is a follow-up that needs the previous turn's
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
        # Short questions and ones that refer back ("it", "that", "this") are the follow-ups
        # most likely to need context; a 7-word "give me an example code for it" must count.
        refers_back = bool(_REFERRING_WORDS.search(clean_query))
        is_short = len(clean_query.split()) <= 12
        is_related = similarity >= 0.30

        if (is_short and is_related) or (refers_back and is_short):
            return f"{clean_query} (Context: regarding {last_user_msg})"

        return query


class QueryContextualizer:
    """
    Turns a follow-up question into a standalone one before routing, retrieval and
    answering, using the conversation so far: "give me an example code for it" after a
    question about transaction management becomes "give me example code for Spring
    transaction management". This is the "contextualize question" step of conversational
    RAG; without it, "it" reaches the router and the search tools with nothing to refer to.

    Only runs when there is history. If the model is unavailable or returns something that
    isn't a usable question, falls back to MemoryLoader.rewrite_query.
    """

    @staticmethod
    def _transcript(history: List[Dict[str, str]]) -> str:
        lines = []
        for message in history[-_CONTEXT_TURNS * 2:]:
            content = (message.get("content") or "").strip()
            if message.get("role") == "assistant":
                if len(content) > _ANSWER_CONTEXT_CHARS:
                    content = content[:_ANSWER_CONTEXT_CHARS] + " …"
                lines.append(f"Assistant: {content}")
            else:
                lines.append(f"User: {content}")
        return "\n".join(lines)

    @staticmethod
    def _clean(output: str, query: str) -> Optional[str]:
        """The model's rewrite, or None if it isn't a usable standalone question."""
        text = output.strip()
        for prefix in ("standalone question:", "rewritten question:", "question:"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # A rewrite is one question, not an answer: several lines, or text far longer than
        # the input, means the model started answering.
        if len(lines) != 1 or len(lines[0]) > max(300, 4 * len(query)):
            return None
        text = lines[0].strip('"').strip("'").strip()
        return text or None

    @classmethod
    async def contextualize(cls, query: str, history: List[Dict[str, str]]) -> ContextualizedQuery:
        if not history:
            return ContextualizedQuery(text=query, method="none")

        from app.core.llm_gateway import llm_gateway, OllamaUnavailableError, LLMBudgetExhaustedError

        prompt = (
            f"Conversation:\n{cls._transcript(history)}\n\n"
            f"Latest question: {query}\n\n"
            "Standalone question:"
        )
        try:
            output = await llm_gateway.agenerate(
                prompt,
                system=CONTEXTUALIZE_SYSTEM_PROMPT,
                options={"temperature": settings.AGENT_TEMPERATURE, "seed": settings.AGENT_SEED, "num_predict": 80},
            )
        except (OllamaUnavailableError, LLMBudgetExhaustedError) as e:
            return ContextualizedQuery(text=MemoryLoader.rewrite_query(query, history), method="heuristic",
                                       detail=f"LLM unavailable: {e}")

        standalone = cls._clean(output, query)
        if standalone is None:
            return ContextualizedQuery(text=MemoryLoader.rewrite_query(query, history), method="heuristic",
                                       detail=f"Unusable rewrite: {output[:120]!r}")
        return ContextualizedQuery(text=standalone, method="llm")

