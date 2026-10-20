#!/usr/bin/env bash
# One-shot backend startup: Ollama (host) + Postgres/pgvector, MCP server, and the
# FastAPI API (all three via docker-compose). Idempotent — safe to re-run.
#
# Usage: scripts/run_backend.sh [options]
#   --with-frontend   Also build and start the `frontend` compose service.
#   --no-ollama       Skip starting/checking Ollama (use this if you run it elsewhere).
#   --rebuild         Pass --build to docker compose (rebuild images before starting).
#   --down            Tear down the compose stack instead of starting it, and exit.
#   -h, --help        Show this help and exit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

WITH_FRONTEND=false
SKIP_OLLAMA=false
REBUILD=false
TEARDOWN=false

for arg in "$@"; do
  case "$arg" in
    --with-frontend) WITH_FRONTEND=true ;;
    --no-ollama) SKIP_OLLAMA=true ;;
    --rebuild) REBUILD=true ;;
    --down) TEARDOWN=true ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (see --help)" >&2
      exit 1
      ;;
  esac
done

# Prefer the `docker compose` plugin; fall back to the standalone `docker-compose` binary.
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available. Install Docker first." >&2
  exit 1
fi

COMPOSE_SERVICES=(postgres mcp-server api)
if $WITH_FRONTEND; then
  COMPOSE_SERVICES+=(frontend)
fi

if $TEARDOWN; then
  echo "Stopping backend services: ${COMPOSE_SERVICES[*]}"
  "${COMPOSE[@]}" stop "${COMPOSE_SERVICES[@]}"
  exit 0
fi

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "No .env found — copying .env.example -> .env (edit it if you need non-default values)."
    cp .env.example .env
  fi
fi

# --- Ollama (runs on the host, not in docker-compose — see docker-compose.yml's
# OLLAMA_BASE_URL=http://host.docker.internal:11434) --------------------------------
if ! $SKIP_OLLAMA; then
  OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
  OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

  if ! command -v ollama >/dev/null 2>&1; then
    echo "WARNING: 'ollama' not found on PATH — LLM generation will be unavailable until it's installed."
    echo "         Install: curl -fsSL https://ollama.com/install.sh | sh"
    echo "         Or re-run with --no-ollama to silence this check."
  else
    if curl -sf "$OLLAMA_URL" >/dev/null 2>&1; then
      echo "Ollama already serving at $OLLAMA_URL."
    else
      echo "Starting Ollama (logs: /tmp/ollama-serve.log)..."
      nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
      disown
      for _ in $(seq 1 15); do
        curl -sf "$OLLAMA_URL" >/dev/null 2>&1 && break
        sleep 1
      done
      if ! curl -sf "$OLLAMA_URL" >/dev/null 2>&1; then
        echo "WARNING: Ollama still not reachable at $OLLAMA_URL after 15s — check /tmp/ollama-serve.log."
      fi
    fi

    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OLLAMA_MODEL"; then
      echo "Model '$OLLAMA_MODEL' already pulled."
    else
      echo "Pulling model '$OLLAMA_MODEL' (first run only — this can take a while)..."
      ollama pull "$OLLAMA_MODEL"
    fi
  fi
else
  echo "Skipping Ollama checks (--no-ollama)."
fi

# --- Postgres + MCP server + API (+ optional frontend) via docker-compose ----------
COMPOSE_UP_ARGS=(up -d)
$REBUILD && COMPOSE_UP_ARGS+=(--build)
COMPOSE_UP_ARGS+=("${COMPOSE_SERVICES[@]}")

echo "Starting: ${COMPOSE_SERVICES[*]}"
"${COMPOSE[@]}" "${COMPOSE_UP_ARGS[@]}"

# `--wait` (Docker Compose v2.17+) blocks until every started service reports healthy
# (per the healthchecks already defined in docker-compose.yml) instead of racing ahead.
if "${COMPOSE[@]}" up --help 2>&1 | grep -q -- '--wait'; then
  "${COMPOSE[@]}" up -d --wait "${COMPOSE_SERVICES[@]}"
else
  echo "Your docker compose version lacks '--wait' — polling health manually..."
  for _ in $(seq 1 60); do
    unhealthy=$("${COMPOSE[@]}" ps --format '{{.Name}} {{.Health}}' 2>/dev/null \
      | grep -v -E 'healthy|^$' || true)
    [ -z "$unhealthy" ] && break
    sleep 2
  done
fi

echo ""
echo "Backend is up:"
echo "  API docs:    http://localhost:8000/docs"
echo "  MCP server:  http://localhost:8765/health"
echo "  Postgres:    localhost:5444"
$WITH_FRONTEND && echo "  Frontend:    http://localhost:3000"
echo ""
echo "Tear down with: scripts/run_backend.sh --down"
