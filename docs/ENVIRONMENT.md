# Environment — Phase 0

## System

| Property | Value |
|----------|-------|
| OS | Ubuntu 24 (linux x86_64) |
| CPU | Intel Core i5-1155G7 (4 cores / 8 threads) |
| RAM | ~5.5 GiB available / ~15.6 GiB total |
| GPU | None (CPU-only execution) |

## Software Versions

| Component | Version |
|-----------|---------|
| Python | **3.11.15** (via `uv` virtualenv `.venv`) |
| Global Python | 3.14.4 (not used — `.venv/bin/python` is authoritative) |
| Docker | 29.7.2 |
| Docker Compose | v5.5.0 |
| PostgreSQL | 16.15 (Docker) |
| pgvector | 0.8.6 |
| Pandoc | 3.11 (downloaded to `.venv/bin/pandoc`) |
| Ollama | Installed (`/usr/local/bin/ollama`) — **NOT reachable** on port 11434 |

## LLM Availability

**e2e baseline: NOT AVAILABLE**

Ollama is installed but the server is not running. All end-to-end benchmarks requiring LLM generation are unavailable.

To enable Ollama:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen2.5:3b
```

Ollama defaults every model's context window to 2048 tokens regardless of what the model
actually supports. `OLLAMA_NUM_CTX` (default `8192`, see `app/config.py` and
`app/core/llm_gateway.py`) is set explicitly on every chat call so `qwen2.5:3b` (32K native
context) isn't silently truncating evidence-heavy prompts.

Remote LLM providers are not implemented — there is no `REMOTE_LLM_URL`/`REMOTE_LLM_KEY`
config. Only local Ollama is supported.

## Models

| Model | Purpose | Notes |
|-------|---------|-------|
| `all-MiniLM-L6-v2` | Dense embedding (384 dim) | Loaded via sentence-transformers |
| `ms-marco-MiniLM-L-6-v2` | Cross-encoder reranker | Loaded via sentence-transformers |
| `qwen2.5:3b` | Default local LLM | Requires Ollama running |

## Memory Constraints

~5.5 GiB available RAM (varies). Models loaded:
- Embedding: ~91 MB
- Reranker: ~83 MB  
- 3B LLM (if Ollama runs): ~1.8 GiB (GGUF q4_K_M)

A 7B model is optional and not required for Phase 0.

## Chunk Sizing

Chunk sizing uses the embedding model's Hugging Face tokenizer (`AutoTokenizer`), **not** `tiktoken` (which is not a dependency).

## Python Version Parity

To ensure Python 3.11 parity without system package changes:
```bash
# Create venv
uv venv .venv --python 3.11
uv pip install -p .venv -r requirements.txt

# Or via Docker
docker run --rm -v $(pwd):/workspace python:3.11-slim bash -c \
  "cd /workspace && pip install -r requirements.txt && pytest tests/ -q"
```
