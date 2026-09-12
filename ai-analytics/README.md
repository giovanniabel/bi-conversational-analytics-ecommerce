# ai-analytics

The Streamlit app package. For project overview, architecture diagrams,
the LangGraph pipeline explanation, and Docker quick start, see the
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
uv run streamlit run app.py
```

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

`test_sql_validator.py` exercises the read-only SQL allowlist directly.
`test_graph.py` drives the LangGraph pipeline with fake LLM/ClickHouse
stubs to verify retry-then-succeed, give-up-after-max-attempts, and that
a retried query always re-enters `validate_sql` — it can never reach
`execute_sql` by skipping validation. Neither needs a live LLM or database.

## Module map

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI |
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
