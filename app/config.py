import os
from typing import List
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Engineering AI Assistant Platform"
    VERSION: str = "2.0.0"
    
    # Environment & Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://raguser:ragpassword@localhost:5444/ragdb")
    
    # Ollama / Local LLM
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    # Ollama defaults to a 2048-token context window for every model regardless of what it
    # natively supports. Qwen2.5:3b supports 32768; 8192 covers this pipeline's worst-case
    # prompt (up to 10 merged evidence chunks + system prompt) with room for generation.
    OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    
    # Models
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
    RERANKER_MODEL_NAME: str = os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    
    # MCP
    MCP_SERVER_HOST: str = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    MCP_SERVER_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8765"))
    MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")
    MCP_API_KEY: str = os.getenv("MCP_API_KEY", "mcp-secret-token")
    
    # Security
    API_KEY: str = os.getenv("API_KEY", "dev-api-key")
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    
    # Chunking & Ingestion (calibrated for 256 max sequence length of all-MiniLM-L6-v2)
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "200"))
    MAX_CHUNK_SIZE: int = int(os.getenv("MAX_CHUNK_SIZE", "240"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "30"))

    # Agent Execution Budgets
    MAX_AGENT_STEPS: int = int(os.getenv("MAX_AGENT_STEPS", "15"))
    MAX_LLM_CALLS: int = int(os.getenv("MAX_LLM_CALLS", "6"))
    MAX_SUB_QUESTIONS: int = 3
    MAX_RETRIES: int = 2
    MAX_TOOL_CALLS: int = 5
    MEMORY_TURNS: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
