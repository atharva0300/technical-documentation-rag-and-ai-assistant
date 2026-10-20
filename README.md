# Engineering AI Assistant Platform

A multi-agent Retrieval-Augmented Generation (RAG) assistant over a bundled corpus of
technical documentation (Kubernetes, Docker, FastAPI, PostgreSQL/pgvector, SQLAlchemy,
Python asyncio), built with **LangGraph**, **Model Context Protocol (MCP)**, **FastAPI**,
**PostgreSQL (pgvector)**, a local **Ollama** LLM (Qwen2.5:3b), and a **Vite + React 19** UI.

It decomposes a question into sub-questions, retrieves evidence via hybrid search
(dense + BM25, fused with RRF, cross-encoder reranked), generates a grounded answer with
inline citations, and runs that answer through a deterministic (zero-LLM) verifier that can
reject or trim an ungrounded claim and trigger a bounded retry before the answer is returned.

---

## System Architecture

```mermaid
graph TD
    User([User / Web Browser]) -->|HTTP / SSE Stream| Proxy[Express Server Proxy]
    Proxy -->|Authenticated REST / SSE| FastAPI[FastAPI Backend /api/v2]

    subgraph "LangGraph Supervisor Graph"
        FastAPI --> MemoryLoader[Memory Loader Node]
        MemoryLoader --> Planner[Planner Agent: Fast-Path + LLM Fallback]

        Planner -->|Sub-questions, one retrieval call each, sequentially| Retriever[Retrieval Agent]

        Retriever -->|search_documentation| MCPClient[MCP Client]
        MCPClient -->|Streamable HTTP, bearer auth| MCPServer[MCP Server :8765]

        subgraph "Hybrid Search Engine"
            MCPServer -->|Vector Cosine| PGVector[(PostgreSQL / pgvector HNSW)]
            MCPServer -->|Technical Token BM25| BM25[(BM25 Lexical Engine)]

            PGVector --> RRF[Reciprocal Rank Fusion]
            BM25 --> RRF
            RRF --> Reranker[Cross-Encoder Reranker]
        end

        Reranker --> Merger[Evidence Merger Node]
        Merger --> Answerer[Answerer Agent]
        Answerer --> Verifier[3-Layer Deterministic Verifier]

        Verifier -->|VALID / PARTIAL| Finish[Final Answer]
        Verifier -->|RETRY: citation format| Answerer
        Verifier -->|RETRY: ungrounded content| Retriever
    end

    Finish -->|SSE: live tokens on the first attempt,<br/>one answer_replaced event if retried| User
```

---

## Key Technical Features

- **LangGraph StateGraph Supervisor**: Planner → Retriever → Evidence Merger → Answerer →
  Verifier, with hard step (`MAX_AGENT_STEPS`), LLM-call, and tool-call budgets enforced
  per request.
- **Hybrid RAG Pipeline**: pgvector cosine ANN search (HNSW index) + BM25 lexical search
  with a technical tokenizer that preserves `--memory`-style flags, `v1.2.3` versions, and
  paths — fused with Reciprocal Rank Fusion and reranked with a cross-encoder
  (`ms-marco-MiniLM-L-6-v2`). `hybrid_rerank` is the only strategy the public API and the
  MCP tool expose; `dense`/`bm25`/`hybrid` remain internal `app.retrieval.search` functions
  used by the offline evaluation scripts to compare strategies.
- **3-Layer Deterministic Verifier**: zero-LLM check of citation syntax/range, technical-token
  grounding, and sentence-level claim support. A citation-format failure (missing/out-of-range
  `[n]` tags) regenerates from the *same* evidence; an ungrounded-content failure re-retrieves.
  Bounded by `MAX_RETRIES`.
- **MCP tool-calling**: a standalone MCP server (Streamable HTTP, bearer-auth) exposes exactly
  one tool, `search_documentation` — kept deliberately small and deliberately real, rather than
  allow-listing tools no planner path can reach.
- **Process-wide circuit breaker**: the MCP client trips after repeated consecutive tool
  failures and stays tripped across requests (not per-request), the way a circuit breaker
  needs to behave to mean anything.
- **Correctly-sized LLM context**: Ollama defaults every model to a 2048-token context window
  regardless of what it actually supports. `OLLAMA_NUM_CTX` (default `8192`) is explicitly set
  on every call so Qwen2.5:3b (32K native context) isn't silently truncating this pipeline's
  evidence-heavy prompts.
- **Real SSE streaming with a correct retry story**: `/api/v2/agent/stream` streams the first
  attempt's tokens live; if the verifier rejects it, the regenerated attempt is buffered
  server-side and delivered as one `answer_replaced` event instead of a second stream of
  tokens silently concatenating onto the first (rejected) one.
- **Thread-scoped memory**: `MemoryLoader` fetches recent DB turns and rewrites short
  follow-up queries into standalone search queries.
- **Express server-side proxy**: `server/index.js` attaches the API key server-side so it
  never reaches the browser bundle — every client call, including document upload, goes
  through `/api/proxy/*`. In production the same process also serves the Vite-built static
  SPA; in dev, Vite's own dev server proxies to this process.

---

## Quickstart: Running Locally

The platform requires 4 local services.

### Prerequisites
1. **Python 3.11+** virtual environment at `.venv/`
2. **Node.js 20+** and `npm`
3. **Docker** (for PostgreSQL pgvector)
4. **Ollama** (local LLM runtime, `ollama pull qwen2.5:3b`)

### Step 1 — Start PostgreSQL pgvector

```bash
docker run -d \
  --name rag-postgres \
  -e POSTGRES_USER=raguser \
  -e POSTGRES_PASSWORD=ragpassword \
  -e POSTGRES_DB=ragdb \
  -p 5444:5432 \
  pgvector/pgvector:pg16
```

### Step 2 — Start the MCP server

```bash
export DATABASE_URL=postgresql://raguser:ragpassword@localhost:5444/ragdb
export MCP_API_KEY=mcp-secret-token
.venv/bin/python -m mcp_server.server
```

### Step 3 — Start the FastAPI backend

```bash
export DATABASE_URL=postgresql://raguser:ragpassword@localhost:5444/ragdb
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:3b
export MCP_API_KEY=mcp-secret-token

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs: **http://localhost:8000/docs**

### Step 4 — Start the frontend

```bash
cd frontend
npm install
API_BACKEND_URL=http://localhost:8000 API_KEY=dev-api-key npm run dev
```

This starts two processes together: the Vite dev server (the SPA, with hot reload) and
the Express proxy server that attaches the API key. In production (`npm run build` +
`npm start`, or Docker) there's only one process — Express serves the built static files
and the proxy together.

Website: **http://localhost:5173** (dev) or **http://localhost:3000** (production/Docker)

### Or: `docker-compose up` brings up all four services together.

---

## Testing & Evaluation Commands

```bash
# Run the test suite (excludes tests that need a live MCP server/Postgres)
make test

# Run the end-to-end agent evaluation suite against the live graph
make eval-agent

# Build the frontend production bundle (Vite)
make build-frontend

# Dataset validation & corpus integrity checks
make test-dataset
```

---

## Repository Structure

```
├── app/
│   ├── agents/          # LangGraph agents (planner, retriever, merger, answerer, verifier, memory, graph)
│   ├── api/v2/          # FastAPI router — the only API surface
│   ├── core/            # Config, LLM gateway
│   ├── database/        # SQLAlchemy models and connection manager
│   ├── embeddings/      # SentenceTransformer embedding model wrapper
│   ├── ingestion/       # Token-aware chunker + ingestion pipeline
│   ├── mcp/             # MCP client with a process-wide circuit breaker
│   └── retrieval/       # Dense, BM25, RRF, cross-encoder reranker, unified search
├── mcp_server/          # MCP server exposing the one search_documentation tool
├── frontend/            # Vite + React 19 SPA (src/) and the Express proxy (server/)
├── scripts/             # eval_agent.py (live graph eval), split_dataset.py
├── tests/               # Unit, agent graph, mcp, api, and security tests
└── docs/                # API reference, retrieval benchmarks, environment variables
```
