# Engineering AI Assistant Platform

A multi-agent Retrieval-Augmented Generation (RAG) assistant over arbitrary uploaded
technical documentation, built with **LangGraph**, **Model Context Protocol (MCP)**,
**FastAPI**, **PostgreSQL (pgvector)**, a local **Ollama** LLM (Qwen2.5:3b), and a
**Vite + React 19** UI.

It classifies each query with an embedding-similarity semantic router (not a keyword list),
selects an MCP tool dynamically from the server's live tool manifest, and routes retrieval
down one of three strategies: plain hybrid search (dense + BM25 + RRF, cross-encoder
reranked) for point lookups, or GraphRAG-lite local/global search for relational and
corpus-wide "scattered across many pages" queries. The generated answer carries inline
citations and is checked by a verifier that grounds each citation against its specific
cited chunk — both lexically and via NLI entailment — before triggering a bounded retry on
an ungrounded claim.

---

## System Architecture

```mermaid
graph TD
    User([User / Web Browser]) -->|HTTP / SSE Stream| Proxy[Express Server Proxy]
    Proxy -->|Authenticated REST / SSE| FastAPI[FastAPI Backend /api/v2]

    subgraph "LangGraph Supervisor Graph"
        FastAPI --> MemoryLoader[Memory Loader Node<br/>warms live MCP tool manifest]
        MemoryLoader --> Planner[Planner Agent:<br/>Semantic Router + LLM Fallback]

        Planner -->|retrieval_strategy| Route{Route by strategy}
        Route -->|point_lookup| InvokeTools[Invoke Tools Node]
        Route -->|relational| LocalSearch[Local Search:<br/>entity graph + vector fusion]
        Route -->|exhaustive| GlobalSearch[Global Search:<br/>map-reduce over community summaries]

        InvokeTools -->|tool resolved from live manifest, not hardcoded| MCPClient[MCP Client]
        MCPClient -->|Streamable HTTP, bearer auth| MCPServer[MCP Server :8765]

        subgraph "Hybrid Search Engine"
            MCPServer -->|Vector Cosine| PGVector[(PostgreSQL / pgvector HNSW)]
            MCPServer -->|Technical Token BM25| BM25[(BM25 Lexical Engine)]

            PGVector --> RRF[Lexical-Specificity-Weighted RRF]
            BM25 --> RRF
            RRF --> Reranker[Cross-Encoder Reranker]
        end

        LocalSearch -.->|falls back if no graph match| InvokeTools
        GlobalSearch -.->|falls back if graph unbuilt/empty| InvokeTools

        Reranker --> Merger[Evidence Merger Node]
        LocalSearch --> Merger
        GlobalSearch --> Merger
        Merger --> Answerer[Answerer Agent]
        Answerer --> Verifier[Verifier: citation syntax +<br/>per-citation token grounding +<br/>NLI entailment]

        Verifier -->|VALID / PARTIAL| Finish[Final Answer]
        Verifier -->|RETRY: citation format| Answerer
        Verifier -->|RETRY: ungrounded content| Route
    end

    Finish -->|SSE: live tokens on the first attempt,<br/>one answer_replaced event if retried| User
```

---

## Key Technical Features

- **LangGraph StateGraph Supervisor**: Memory Loader → Planner → one of three retrieval
  nodes (by `retrieval_strategy`) → Evidence Merger → Answerer → Verifier, with hard step
  (`MAX_AGENT_STEPS`), LLM-call, and tool-call budgets enforced per request.
- **Semantic router, not a keyword list**: intent/retrieval-strategy classification
  (`app/retrieval/semantic_router.py`) matches the query against example utterances per
  category via embedding cosine similarity, generalizing across phrasing instead of
  enumerating it — falls through to an LLM classification pass only below a confidence
  threshold.
- **Live, generic MCP tool selection**: the planner resolves which tool to call by
  inspecting the MCP server's *live* `list_tools()` manifest (`app/mcp/client.py`), never a
  hardcoded tool name. Adding, renaming, or removing a tool on the server needs no planner
  change. A single `_invoke_selected_tool` helper is the only place any tool is actually
  called, shared by all three retrieval strategies.
- **GraphRAG-lite** (`app/graph/`): entity extraction + Louvain community detection over the
  corpus. "Relational" queries ("what depends on X") get entity-anchored graph traversal
  fused into RRF as a third ranked source; "exhaustive" queries ("every place X is
  mentioned") get map-reduce over pre-built community summaries — the actual fix for
  answers scattered across many pages, which widening `top_k` cannot cover.
- **Hybrid RAG Pipeline**: pgvector cosine ANN search (HNSW index) + BM25 lexical search
  with a technical tokenizer that preserves `--memory`-style flags, `v1.2.3` versions, and
  paths — fused with a lexical-specificity-weighted Reciprocal Rank Fusion (the query's own
  BM25 IDF profile against the current corpus biases the dense/BM25 blend per query, with
  no hardcoded vocabulary) and reranked with a cross-encoder (`ms-marco-MiniLM-L-6-v2`).
- **Verifier**: citation syntax/range check, then per-citation (not pooled) technical-token
  grounding, then NLI entailment (`cross-encoder/nli-deberta-v3-small`) between the cited
  chunk and the sentence citing it — catches "right term, wrong claim" errors a lexical-only
  check misses. A citation-format failure regenerates from the *same* evidence; an
  ungrounded-content failure re-retrieves via the same `retrieval_strategy`. Bounded by
  `MAX_RETRIES`.
- **MCP tool-calling**: a standalone MCP server (Streamable HTTP, bearer-auth) currently
  exposes one tool, `search_documentation` — but nothing in the client/planner path assumes
  that; see "Live, generic MCP tool selection" above.
- **Process-wide circuit breaker with half-open recovery**: the MCP client trips after
  repeated consecutive tool failures and stays tripped across requests (not per-request).
  After a cooldown it allows exactly one trial call through (half-open); success closes the
  breaker again, failure reopens it — not a one-way trip requiring a process restart.
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

The platform requires 4 local services. `scripts/run_backend.sh` brings up the three
backend ones (Postgres, MCP server, API) in one command — see below. The frontend is still
started separately.

### Prerequisites
1. **Python 3.11+** virtual environment at `.venv/`
2. **Node.js 20+** and `npm`
3. **Docker** (for PostgreSQL pgvector)
4. **Ollama** (local LLM runtime, `ollama pull qwen2.5:3b`)

### Fastest path — one script

```bash
./scripts/run_backend.sh
```

Starts/confirms Ollama (serving + model pulled), brings up `postgres`, `mcp-server`, and
`api` via `docker compose`, waits for all three to report healthy, and prints the API docs
URL. Pass `--with-frontend` to also build and start the `frontend` compose service. Run it
with `--help` for every flag (`--no-ollama`, `--rebuild`, `--down`, ...). This is what the
manual steps below do by hand.

### Manual steps (what the script automates)

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
│   ├── graph/           # GraphRAG-lite: entity extraction, community detection, local/global search
│   ├── ingestion/       # Token-aware chunker + ingestion pipeline
│   ├── mcp/             # MCP client: half-open circuit breaker + live tool manifest cache
│   └── retrieval/       # Dense, BM25, RRF, semantic router, query analysis, reranker, unified search
├── mcp_server/          # MCP server exposing the one search_documentation tool
├── frontend/            # Vite + React 19 SPA (src/) and the Express proxy (server/)
├── scripts/             # run_backend.sh (one-shot backend startup), eval_agent.py, split_dataset.py
├── tests/               # Unit, agent graph, mcp, api, and security tests
└── docs/                # API reference, retrieval benchmarks, environment variables
```
