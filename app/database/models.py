from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=True)
    source = Column(String, nullable=True)
    url = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), index=True, nullable=True)
    chunking_config = Column(String, nullable=True)
    embedding_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    section = Column(String, nullable=True)
    source = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    # bge-base-en-v1.5 output dimension (768). Was Vector(384) for
    # all-MiniLM-L6-v2 -- MiniLM is an STS (sentence-similarity) model, not
    # retrieval-trained; bge/e5-family models use a contrastive query-passage
    # objective and measurably outperform it on MTEB's retrieval benchmark.
    # Changing this dimension requires re-embedding the corpus (see
    # app.embeddings.model and docs/ENVIRONMENT.md).
    embedding = Column(Vector(768))
    metadata_json = Column(Text, nullable=True)
    # GraphRAG-lite fields (see app/graph/): which Louvain community this
    # chunk was assigned to, and its cached extracted entities, so repeated
    # graph queries don't re-run entity extraction on every request.
    community_id = Column(Integer, nullable=True, index=True)
    entities_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="chunks")


class CommunitySummary(Base):
    """
    One row per Louvain community detected in the chunk-entity graph (see
    app/graph/community_builder.py). `summary_text` is generated once per
    community at graph-build time (LLM-assisted, with a deterministic
    fallback when the LLM is unavailable) and is what GraphRAG-lite's global
    search map-reduces over for exhaustive/aggregation-style queries, instead
    of ranking individual chunks.
    """
    __tablename__ = "community_summaries"

    id = Column(Integer, primary_key=True, index=True)
    community_id = Column(Integer, unique=True, index=True, nullable=False)
    summary_text = Column(Text, nullable=False)
    chunk_count = Column(Integer, default=0)
    entity_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, index=True) # UUID string
    title = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False) # user / assistant / system
    content = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=True) # JSON representation of source citations
    agent_trace_json = Column(Text, nullable=True) # JSON representation of reasoning steps
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")

