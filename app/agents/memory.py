from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.database.connection import SessionLocal
from app.database.models import Message
from app.config import settings

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
        if not history:
            return query
            
        clean_query = query.strip()
        lower_q = clean_query.lower()

        # Do not rewrite greetings or greetings fast-paths
        if lower_q in ["hi", "hello", "hey", "good morning", "good evening", "who are you"]:
            return query

        # Follow-up indicators like "what about X", "how to configure it", "why", "and Y"
        followup_triggers = ["what about", "how about", "why", "how to configure it", "explain more", "give an example"]
        
        is_followup = any(trig in lower_q for trig in followup_triggers) or len(clean_query.split()) <= 4
        
        if is_followup and history:
            last_user_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
            if last_user_msg:
                return f"{clean_query} (Context: regarding {last_user_msg})"

        return query
