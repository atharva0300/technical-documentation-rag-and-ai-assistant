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
| Ollama | 0.34.2 (`/usr/local/bin/ollama`), serving on `0.0.0.0:11434` |
| `mcp` (Python SDK) | 2.3.0 (pinned in `requirements.txt`) |

## LLM Availability

The API calls a local Ollama server (`OLLAMA_BASE_URL`, `OLLAMA_MODEL=qwen2.5:3b`).

```bash
curl -fsSL https://ollama.com/install.sh | sh
OLLAMA_HOST=0.0.0.0:11434 ollama serve &
ollama pull qwen2.5:3b
```

When the API runs in Docker it reaches the host's Ollama at
`http://host.docker.internal:11434` (mapped to the host gateway in `docker-compose.yml`).
That only works if Ollama listens on all interfaces (`OLLAMA_HOST=0.0.0.0:11434`; the
default is `127.0.0.1`) and the host firewall allows Docker networks to reach port 11434
(ufw: `sudo ufw allow in from 172.16.0.0/12 to any port 11434 proto tcp`). If Ollama is
unreachable, the answerer falls back to returning raw excerpts.

Ollama defaults every model's context window to 2048 tokens regardless of what the model
actually supports. `OLLAMA_NUM_CTX` (default `8192`, see `app/config.py` and
`app/core/llm_gateway.py`) is set explicitly on every chat call so `qwen2.5:3b` (32K native
context) isn't silently truncating evidence-heavy prompts.

Remote LLM providers are not implemented — there is no `REMOTE_LLM_URL`/`REMOTE_LLM_KEY`
config. Only local Ollama is supported.

## Models

| Model | Purpose | Notes |
|-------|---------|-------|
| `BAAI/bge-base-en-v1.5` | Dense embedding (768 dim) | Loaded via sentence-transformers. Retrieval-contrastive-trained, not an STS model like the previous `all-MiniLM-L6-v2` (384 dim) — measurably stronger on MTEB's retrieval benchmark. Switching embedding models requires re-embedding the corpus (new column dimension + HNSW rebuild). |
| `cross-encoder/nli-deberta-v3-small` | NLI entailment check (verifier Layer 3) | Loaded via sentence-transformers. Checks whether a cited chunk actually entails an answer sentence, not just lexical token overlap — same role production tools like Vectara's HHEM play in RAG faithfulness checking. |
| `ms-marco-MiniLM-L-6-v2` | Cross-encoder reranker | Loaded via sentence-transformers |
| `qwen2.5:3b` | Default local LLM | Requires Ollama running |

Model names come from `app/config.py` (`EMBEDDING_MODEL_NAME`, `RERANKER_MODEL_NAME`,
`NLI_MODEL_NAME`), which every component reads; set them there or via environment variables
of the same names.

## Memory Constraints

~5.5 GiB available RAM (varies). Models loaded:
- Embedding (bge-base-en-v1.5): ~440 MB
- NLI entailment model: ~280 MB
- Reranker: ~83 MB  
- 3B LLM (if Ollama runs): ~1.8 GiB (GGUF q4_K_M)

A 7B model is optional and not required for Phase 0.

## Chunk Sizing

Chunk sizing uses the embedding model's Hugging Face tokenizer (`AutoTokenizer`), **not** `tiktoken` (which is not a dependency).
Defaults are `MAX_CHUNK_SIZE=240` / `CHUNK_OVERLAP=30` tokens, within bge-base's 512-token limit.

Ingestion embeds chunks in batches. On this CPU, the ~900-page Spring Framework reference
PDF (~2,900 chunks) takes ~13 minutes end to end.

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
