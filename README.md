# Engineering AI Assistant Platform

A multi-agent Retrieval-Augmented Generation (RAG) assistant over arbitrary uploaded
technical documentation, built with **LangGraph**, **Model Context Protocol (MCP)**,
**FastAPI**, **PostgreSQL (pgvector)**, a local **Ollama** LLM (Qwen2.5:3b), and a
**Vite + React 19** UI.

Follow-up questions are first rewritten into standalone ones from the conversation. A cheap
embedding-similarity router turns away greetings and off-topic requests; everything else
goes to a bounded tool-calling loop in which the model itself chooses which MCP tools to
call and with what arguments, via Ollama's native tool calling: hybrid search (dense + BM25
+ RRF, cross-encoder reranked), code-only search, whole sections, the table of contents, and
GraphRAG-lite related/topic search. The generated answer carries inline citations and is
checked by a verifier that grounds each citation against its specific cited chunk — both
lexically and via NLI entailment — before triggering a bounded retry on an ungrounded claim.

---

## Benchmarks

Measured end to end on the live stack (real Ollama model, real Postgres/pgvector, real MCP
server — no mocking), on a stratified 40-question sample of a 139-question labeled
evaluation set:

| Metric | Value |
|---|---|
| Planner intent accuracy | 80.6% |
| Grounding / verification validity rate | 86.1% |
| p50 latency | 55.9s |
| p95 latency | 83.6s |

Full methodology, per-category breakdown, raw results, and a router miscalibration found
and fixed while measuring these: [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

---

## System Architecture

![Agent graph: memory and follow-up rewriting, router fast path, model-driven MCP tool-calling loop, evidence merger, answerer and verifier](docs/images/agent-graph.png)

The agent graph above is rendered from [`docs/images/agent-graph.dot`](docs/images/agent-graph.dot)
(`dot -Tpng -Gdpi=160 docs/images/agent-graph.dot -o docs/images/agent-graph.png`); its nodes
and edges follow the compiled LangGraph in `app/agents/graph.py`.

![System architecture: frontend, api, mcp-server and postgres services with host Ollama; the ingestion pipeline; GraphRAG-lite indexing and its two query-time tools](docs/images/system-architecture.png)

The system view above ([`docs/images/system-architecture.dot`](docs/images/system-architecture.dot))
places the agent graph among the docker-compose services, and shows the two pipelines that run
outside it: per-document ingestion on upload, and GraphRAG-lite indexing (orange), a corpus-wide
batch triggered by `POST /api/v2/documents/graph/rebuild` that extracts entities, links chunks by
shared entities, detects Louvain communities and summarizes them. At query time GraphRAG is two
MCP tools the agent can choose: `find_related` (graph traversal fused into hybrid search) and
`survey_topic` (the most relevant community summaries). The Mermaid diagram below shows the
request flow end to end, from the browser to the database.

```mermaid
graph TD
    User([User / Web Browser]) -->|HTTP / SSE Stream| Proxy[Express Server Proxy]
    Proxy -->|Authenticated REST / SSE| FastAPI[FastAPI Backend /api/v2]

    subgraph "LangGraph Supervisor Graph"
        FastAPI --> MemoryLoader[Memory Loader Node:<br/>rewrites follow-ups into standalone questions]
        MemoryLoader --> Planner[Planner:<br/>semantic router fast path]

        Planner -->|greeting / off-topic| Answerer
        Planner -->|everything else| Agent[Tool-calling agent:<br/>model picks tools + arguments,<br/>sees results, decides next call]

        Agent <-->|native tool calls, validated against MCP schemas| MCPClient[MCP Client]
        MCPClient -->|Streamable HTTP, bearer auth| MCPServer[MCP Server :8765]

        subgraph "MCP tools"
            MCPServer --> Search[search_documentation /<br/>search_code_examples]
            MCPServer --> Nav[get_section / expand_passage /<br/>list_documents / get_table_of_contents]
            MCPServer --> Graph[find_related / survey_topic<br/>GraphRAG-lite]
            Search -->|dense + BM25 + RRF + cross-encoder| PGVector[(PostgreSQL / pgvector)]
            Nav --> PGVector
            Graph --> PGVector
        end

        Agent --> Merger[Evidence Merger Node]
        Merger --> Answerer[Answerer Agent]
        Answerer --> Verifier[Verifier: citation syntax +<br/>per-citation token grounding +<br/>NLI entailment]

        Verifier -->|VALID / PARTIAL| Finish[Final Answer]
        Verifier -->|RETRY: citation format| Answerer
        Verifier -->|RETRY: ungrounded content| Agent
        Verifier -->|out of retries| Refuse[Refusal instead of an unverified draft]
    end

    Finish -->|SSE: live tokens on the first attempt,<br/>one answer_replaced event if retried| User
```

---

## Key Technical Features

- **LangGraph StateGraph Supervisor**: Memory Loader → Planner → tool-calling agent →
  Evidence Merger → Answerer → Verifier, with hard step (`MAX_AGENT_STEPS`), LLM-call
  (`MAX_LLM_CALLS`), and tool-call (`MAX_TOOL_CALLS`) budgets enforced per request.
- **Model-driven tool calling** (`app/agents/tool_agent.py`): the live MCP manifest is
  passed to Ollama as native function tools. The model emits `tool_calls` with its own
  arguments (e.g. a reformulated query), sees each result as a `tool` message, and decides
  whether to call again or stop, for up to `AGENT_MAX_TOOL_ROUNDS` rounds; calls in one
  round run concurrently. Tool-selection turns run at temperature 0 with a fixed seed.
  Guardrails: tool names are checked against the manifest, arguments against the tool's
  MCP JSON schema (unknown arguments rejected with an error the model can act on), `top_k`
  is clamped, and a plain search runs if the model is unavailable or gathers no passages
  for a question the router confidently classed as needing the docs.
- **Semantic router as a fast path**: `app/retrieval/semantic_router.py` matches the query
  against example utterances per category via embedding cosine similarity, so greetings
  and clearly off-topic requests are answered without any LLM call. It never selects
  tools. The categories that skip retrieval (chitchat, out-of-scope) need 0.65 similarity,
  calibrated for bge-base so a real question like "what is spring?" isn't answered with a
  canned greeting.
- **Follow-up contextualization** (`QueryContextualizer`, `app/agents/memory.py`): when
  there is history, one LLM call rewrites the question as a standalone one ("give me an
  example code for it" → "give me example code for Spring transaction management"), which
  then drives routing, retrieval and the answer. Falls back to an embedding heuristic if
  the model is unavailable.
- **GraphRAG-lite** (`app/graph/`): entity extraction + Louvain community detection over the
  corpus, exposed as two MCP tools: `find_related` (entity-anchored graph traversal fused
  into RRF) for "what depends on X", and `survey_topic` (the most relevant pre-built
  community summaries) for answers scattered across many pages. Build the graph with
  `POST /api/v2/documents/graph/rebuild`.
- **Hybrid RAG Pipeline**: pgvector cosine ANN search (HNSW index) + BM25 lexical search
  with a technical tokenizer that preserves `--memory`-style flags, `v1.2.3` versions, and
  paths — fused with a lexical-specificity-weighted Reciprocal Rank Fusion (the query's own
  BM25 IDF profile against the current corpus biases the dense/BM25 blend per query, with
  no hardcoded vocabulary) and reranked with a cross-encoder (`ms-marco-MiniLM-L-6-v2`).
- **Ingestion**: PDF/Markdown/text are cleaned and chunked with the embedding model's own
  tokenizer, then embedded in batches (`generate_embeddings_batch`). Numbered PDF section
  headings ("7.14 Registering a LoadTimeWeaver") become each chunk's section, so citations
  read `7.14 Registering a LoadTimeWeaver · p. 170` instead of only a page number, and
  table-of-contents dot-leader lines are dropped so they don't crowd real sections out of
  keyword search. Uploads run in a worker thread, so a long ingest doesn't block other
  requests.
- **Natural, inline-cited answers**: the answerer prompt asks for a direct answer followed
  by an explanation in prose, with `[n]` placed right after the sentence it supports
  (rather than a source-by-source dump), and Markdown/code blocks where useful. The
  frontend renders answers as Markdown (`react-markdown` + `remark-gfm`) and turns each
  inline `[n]` into a chip that opens that source.
- **Verifier**: citation syntax/range check, then per-citation (not pooled) technical-token
  grounding, then NLI entailment (`cross-encoder/nli-deberta-v3-small`) between the cited
  chunk and the sentence citing it — catches "right term, wrong claim" errors a lexical-only
  check misses. A citation-format failure regenerates from the *same* evidence; an
  ungrounded-content failure goes back to the tool-calling agent with the verifier's
  feedback. Bounded by `MAX_RETRIES`; a run that ends without a verified, cited answer
  returns a refusal rather than the unverified draft. Unsupported sentences are removed from the answer in place, keeping its
  paragraphs, lists and code blocks intact.
- **MCP server**: a standalone MCP server (Streamable HTTP, bearer-auth) exposes eight
  tools with typed, described input schemas: `search_documentation`,
  `search_code_examples`, `get_section`, `expand_passage`, `list_documents`,
  `get_table_of_contents`, `find_related`, `survey_topic` (see
  [docs/API_ENDPOINTS.md](docs/API_ENDPOINTS.md#3-mcp-tools)). Any MCP client can use them,
  not just this app. Code is detected at ingestion, including unfenced Java/XML in PDFs,
  so code search works on PDF documentation. The server runs one warm-up search at
  startup (in the background) so the first real query doesn't pay the ~20s model-load and
  BM25-index cost and time out. `mcp` is pinned (`mcp==2.3.0`) because its client API
  changed shape between releases.
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
4. **Ollama** (local LLM runtime, `ollama pull qwen2.5:3b`). When the API runs in Docker,
   Ollama must listen beyond loopback — see [Ollama and Docker](#ollama-and-docker).

### Fastest path — one script

```bash
./scripts/run_backend.sh
```

Starts/confirms Ollama (serving on `0.0.0.0:11434` + model pulled), brings up `postgres`, `mcp-server`, and
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
export DATABASE_URL=postgresql+psycopg2://raguser:ragpassword@localhost:5444/ragdb
export MCP_API_KEY=mcp-secret-token
.venv/bin/python -m mcp_server.server
```

### Step 3 — Start the FastAPI backend

```bash
export DATABASE_URL=postgresql+psycopg2://raguser:ragpassword@localhost:5444/ragdb
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

### Ollama and Docker

Ollama runs on the host; the `api` container reaches it at `http://host.docker.internal:11434`
(`docker-compose.yml` maps that name to the host gateway, which Linux needs). Two things
must hold for that to work:

1. **Ollama listens on all interfaces**, not just `127.0.0.1`. `run_backend.sh` starts it
   that way; if you start it yourself, use `OLLAMA_HOST=0.0.0.0:11434 ollama serve`.
2. **The host firewall lets Docker networks reach port 11434.** With ufw:
   `sudo ufw allow in from 172.16.0.0/12 to any port 11434 proto tcp`

Check it from inside the container:
```bash
docker compose exec api curl -s http://host.docker.internal:11434/api/version
```

If Ollama can't be reached, answers fall back to raw excerpts introduced with "The language
model is unavailable right now", which is the sign to check the two points above.

### Uploading large documents

Ingestion is CPU-bound: a ~900-page PDF (the Spring Framework reference) takes ~10–13
minutes to chunk and embed. The API stays responsive meanwhile, but the frontend proxy's
request may time out after 5 minutes even though ingestion completes. For large files,
upload straight to the API:
```bash
curl -X POST http://localhost:8000/api/v2/documents/upload \
  -H "X-API-Key: dev-api-key" -F "file=@path/to/document.pdf"
```
Re-uploading a file with the same name replaces its chunks in place.

---

## Testing & Evaluation Commands

```bash
# Run the test suite (excludes tests that need a live MCP server/Postgres)
make test

# Run the 6-query smoke-test evaluation suite against the live graph
make eval-agent

# Run the full benchmark harness against the real 139-question labeled dataset
# (data/evaluation/candidates.jsonl) through the live HTTP API end to end.
# Defaults to a stratified sample (see docs/BENCHMARKS.md for why); pass --full
# to run every question instead.
python3 scripts/eval_agent_full.py --sample 40 --seed 42 --out docs/eval_raw_results.json

# Build the frontend production bundle (Vite)
make build-frontend

# Dataset validation & corpus integrity checks
make test-dataset
```

Measured results from the benchmark harness — planner intent accuracy, answer
grounding/verification validity rate, and p50/p95 latency, all from real runs against the
live stack (no mocking) — are written up in [docs/BENCHMARKS.md](docs/BENCHMARKS.md),
along with the methodology and a real router miscalibration found and fixed while
measuring them.

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
