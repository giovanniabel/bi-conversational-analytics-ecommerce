# ai-analytics

The application package — it serves **both** UIs from one image:

- `app.py` — the Streamlit UI (port 8501), the Dockerfile's default command
- `web/` — the FastAPI + Metabase UI (port 8000), selected by overriding
  `command` in docker-compose

They share everything in `src/`, so the SQL safety gate and the LLM factory
exist once. For project overview, architecture diagrams, the LangGraph
pipeline explanation, and Docker quick start, see the
[repo root README](../README.md) — this file only covers app-level dev
workflow (running outside Docker, switching LLM providers, running tests).

## Local dev setup (outside Docker)

Requires an already-running ClickHouse (e.g. `docker compose up -d clickhouse`
from the repo root) and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# edit .env: set LLM_PROVIDER, LLM_API_KEY, LLM_MODEL
# CLICKHOUSE_HOST=localhost when running the app directly on your host

uv sync

# UI 1 — Streamlit
uv run streamlit run app.py

# UI 2 — FastAPI + Metabase (needs Metabase running:
#   docker compose up -d metabase   from the repo root)
uv run uvicorn web.main:app --reload --port 8000
```

The FastAPI app provisions Metabase on first use. To do it explicitly —
useful when you want the starter dashboard built before opening the UI:

```bash
uv run python -m src.metabase_provision
```

Running outside Docker, keep `METABASE_INTERNAL_URL=http://localhost:3000`
but leave `METABASE_CLICKHOUSE_HOST=clickhouse`: the first is how *this*
process reaches Metabase, the second is how *Metabase* reaches ClickHouse
on the Compose network.

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. If
you don't have `uv`, `pip install uv` first, or fall back to
`python -m venv .venv && pip install .`.

## Switching LLM providers

Nothing outside `src/llm.py` imports a provider-specific package. Change
two env vars and restart:

| `LLM_PROVIDER` | `LLM_MODEL` example        | requires |
|---|---|---|
| `gemini`    | `gemini-2.5-flash`   | `LLM_API_KEY` = Google AI Studio key |
| `openai`    | `gpt-4o-mini`        | `LLM_API_KEY` = OpenAI key |
| `anthropic` | `claude-sonnet-5`    | `LLM_API_KEY` = Anthropic key |

## Tests

```bash
uv run pytest tests/ -v
```

All 72 run without a live LLM, database, or Metabase:

| File | What it pins down |
|---|---|
| `test_sql_validator.py` | the read-only allowlist, directly |
| `test_graph.py` | retry-then-succeed, give-up-after-max-attempts, and that a retried query always re-enters `validate_sql` — it can never reach `execute_sql` by skipping validation |
| `test_metabase_agent.py` | writes never reach Metabase: a rejected query is neither executed nor saved as a card |
| `test_metabase_client.py` | adding a dashboard card preserves the cards already there (getting this wrong silently wipes a dashboard) and stacks below them |
| `test_metabase_embed.py` | embed JWT claims, expiry, and the no-secret fallback |

## Module map

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI |
| `web/main.py` | FastAPI app: chat, embed URLs, status |
| `web/static/` | chat + embedded-dashboard front end |
| `src/config.py` | env-driven configuration |
| `src/clickhouse_client.py` | read-only ClickHouse client + `QueryResult` |
| `src/schema_inspector.py` | dynamic schema discovery (no hard-coded columns) |
| `src/llm.py` | LangChain chat-model factory — the provider swap point |
| `src/sql_generator.py` | LLM SQL generation via LangChain structured output |
| `src/sql_validator.py` | allowlist-based read-only SQL safety gate |
| `src/graph.py` | LangGraph `StateGraph`: the pipeline + bounded retries |
| `src/analytics_agent.py` | orchestrator: conversation memory + graph invocation |
| `src/result_analyzer.py` | LLM result interpretation, grounded in query data |
| `src/visualization.py` | Plotly chart selection from result shape |
| `src/conversation.py` | sliding-window conversation memory |
| `src/metabase_client.py` | Metabase REST client (session, cards, dashboards, queries) |
| `src/metabase_provision.py` | idempotent first-boot Metabase setup |
| `src/metabase_agent.py` | Claude tool-calling agent that reads and builds Metabase content |
| `src/metabase_embed.py` | signed (JWT) embed URLs |
