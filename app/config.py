import os
from typing import List
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Engineering AI Assistant Platform"
    VERSION: str = "2.0.0"
    
    # Environment & Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://raguser:ragpassword@localhost:5444/ragdb")
    
    # Ollama / Local LLM
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    # Ollama defaults to a 2048-token context window for every model regardless of what it
    # natively supports. Qwen2.5:3b supports 32768; 8192 covers this pipeline's worst-case
    # prompt (up to 10 merged evidence chunks + system prompt) with room for generation.
    OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    
    # Models
    # bge-base-en-v1.5: retrieval-contrastive-trained (not STS-trained like
    # the previous all-MiniLM-L6-v2), 768-dim. See app/embeddings/model.py.
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-base-en-v1.5")
    RERANKER_MODEL_NAME: str = os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    # Faithfulness/entailment model for verifier Layer 3 (see app/agents/verifier.py).
    # A small NLI cross-encoder -- same cost class as RERANKER_MODEL_NAME, CPU-cheap --
    # used the way Vectara's HHEM is used in production RAG pipelines: scoring whether
    # a generated sentence is actually entailed by its cited source, not just whether
    # the sentence's words happen to overlap with some evidence chunk.
    NLI_MODEL_NAME: str = os.getenv("NLI_MODEL_NAME", "cross-encoder/nli-deberta-v3-small")
    
    # MCP
    MCP_SERVER_HOST: str = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    MCP_SERVER_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8765"))
    MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")
    MCP_API_KEY: str = os.getenv("MCP_API_KEY", "mcp-secret-token")
    # Degraded-mode-only fallback: the planner resolves which tool to call by
    # inspecting the MCP server's LIVE tool manifest (app.mcp.client.list_tools_async),
    # never by a hardcoded name in decision logic. This setting is only consulted when
    # that live manifest is empty (server unreachable, or this is the very first
    # request before anything has warmed the cache) -- a resilience fallback, not the
    # selection mechanism, the same way a service-mesh client falls back to a static
    # endpoint only when discovery itself is unavailable. Set to "" to disable the
    # fallback entirely (retrieval is then simply skipped until the manifest resolves).
    DEFAULT_RETRIEVAL_TOOL_FALLBACK: str = os.getenv("DEFAULT_RETRIEVAL_TOOL_FALLBACK", "search_documentation")
    
    # Security
    API_KEY: str = os.getenv("API_KEY", "dev-api-key")
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    
    # Chunking & Ingestion (240 is conservative for either the previous 256-token
    # all-MiniLM-L6-v2 limit or bge-base-en-v1.5's larger 512-token limit)
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "200"))
    MAX_CHUNK_SIZE: int = int(os.getenv("MAX_CHUNK_SIZE", "240"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "30"))

    # Agent Execution Budgets
    MAX_AGENT_STEPS: int = int(os.getenv("MAX_AGENT_STEPS", "15"))
    # The tool-calling retrieval loop (app/agents/tool_agent.py) uses up to
    # AGENT_MAX_TOOL_ROUNDS calls on top of answer generation and verifier retries.
    MAX_LLM_CALLS: int = int(os.getenv("MAX_LLM_CALLS", "8"))
    MAX_SUB_QUESTIONS: int = 3
    MAX_RETRIES: int = 2
    MAX_TOOL_CALLS: int = 5

    # Tool-calling retrieval loop (app/agents/tool_agent.py). The model decides whether
    # and what to search via Ollama's native tool calling; these bound and steady it.
    AGENT_MAX_TOOL_ROUNDS: int = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "3"))
    # Tool selection is a control decision, so it runs greedy (temperature 0, fixed seed)
    # to be as repeatable as the model allows; answer generation keeps the model defaults.
    AGENT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "0"))
    AGENT_SEED: int = int(os.getenv("AGENT_SEED", "42"))
    # Tool-call turns only need a tool call or a short "done" note.
    AGENT_NUM_PREDICT: int = int(os.getenv("AGENT_NUM_PREDICT", "160"))
    # Bounds applied to the model's own top_k argument.
    AGENT_MIN_TOP_K: int = int(os.getenv("AGENT_MIN_TOP_K", "3"))
    AGENT_MAX_TOP_K: int = int(os.getenv("AGENT_MAX_TOP_K", "8"))
    MEMORY_TURNS: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
