from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text

from app.config import settings
from app.api.v2.routes import router as v2_router
from app.database.connection import engine
from app.database.models import Base
from app.observability.tracing import setup_telemetry

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup OpenTelemetry
    setup_telemetry(app)

    # Ensure vector extension & create tables + HNSW index
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
        
    Base.metadata.create_all(bind=engine)

    # Create HNSW index for ANN vector search if chunks table exists
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx "
                "ON chunks USING hnsw (embedding vector_cosine_ops);"
            ))
            conn.commit()
        except Exception as e:
            print(f"HNSW index creation note (safe to ignore if empty table): {e}")

    yield

app = FastAPI(
    title="Engineering AI Assistant Platform API",
    description="Stateful LangGraph Supervisor Agent, Hybrid RRF Retrieval, MCP Server, and Grounded QA Platform.",
    version="2.0.0",
    lifespan=lifespan
)

# Enable CORS for browser and proxy requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v2_router)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
