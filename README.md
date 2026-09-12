# AI Conversational Analytics over ClickHouse

Ask questions about a real e-commerce dataset in plain English and get back
a grounded, data-backed answer — with the generated SQL, execution metadata,
and a chart, all visible for inspection. Built as a schema-aware,
self-correcting pipeline (not a thin "prompt-to-SQL" wrapper) using
**LangChain** + **LangGraph** on top of an existing **ClickHouse** database.

```
"Which product category generates the most revenue?"
→ discovers the schema → generates ClickHouse SQL → validates it's read-only
→ executes it → interprets the actual rows returned → answers + charts it
```

## Why this isn't just "question → SQL → answer"

Most LLM-SQL demos skip straight from a question to a query. This project
treats each stage as a distinct, inspectable step — schema retrieval, SQL
generation, validation, execution, result analysis, and visualization — and
implements the SQL generate/validate/execute loop as an explicit
**LangGraph state machine** with bounded retries, so a bad query gets fed
back to the LLM as correction context instead of crashing or looping forever.

## Quick start (fresh machine)

Requires Docker + Docker Compose and a (free) Kaggle account — the dataset
is downloaded through Kaggle's own API under your account rather than
committed to this repo (see [Dataset &amp; license](#dataset--license) for why).
If you don't have Kaggle API access set up yet, do that first:
[Setting up Kaggle API access](#setting-up-kaggle-api-access).

```bash
git clone https://github.com/giovanniabel/bi-ai.git bi-conversation-analytics-ecommerce
cd bi-conversation-analytics-ecommerce

cp ai-analytics/.env.example ai-analytics/.env
# edit ai-analytics/.env: set LLM_API_KEY (and LLM_PROVIDER/LLM_MODEL if not using Gemini)

./scripts/download_dataset.sh

docker compose up -d
```

Open **http://localhost:8501**. On first startup, ClickHouse automatically:

1. Creates the `olist` database and its 9 tables
2. Loads the full [Olist Brazilian e-commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
   (~1.55M rows) from the CSVs `download_dataset.sh` just placed in [`data/olist/`](data/olist)

The ClickHouse load only happens once, against a fresh Docker volume — see
[Automatic data provisioning](#automatic-data-provisioning) for exactly how
and why. Loading ~1.55M rows takes roughly 30–60s; the schema browser in the
sidebar will fill in once it's done.

### Setting up Kaggle API access

One-time setup, needed before `./scripts/download_dataset.sh` will work.
Credentials go in `ai-analytics/.env` alongside the rest of this project's
config — one file to fill in, no separate `~/.kaggle/kaggle.json`.

1. **Create a free Kaggle account**, if you don't already have one, at
   https://www.kaggle.com/account/login.
2. **Install the Kaggle CLI**: `pip install kaggle` (or `uv tool install kaggle` /
   `pipx install kaggle`).
3. **Create an API token**: go to https://www.kaggle.com/settings, scroll to
   the **API** section, and click **Create New Token**. Kaggle shows your
   username and the generated key on screen — copy both. (Older Kaggle
   accounts may instead get a `kaggle.json` download containing the same
   two values — either way, you just need the username and key.)
4. **Put them in `ai-analytics/.env`**:
   ```env
   KAGGLE_USERNAME=your-username
   KAGGLE_KEY=your-key
   ```

   `scripts/download_dataset.sh` reads these from that file automatically.
   If you'd rather not store them there, exporting `KAGGLE_USERNAME`/
   `KAGGLE_KEY` in your shell works too and takes precedence over the file.
5. **Run the download**: `./scripts/download_dataset.sh`. If it fails with
   a 403/"forbidden" error, open the
   [dataset page](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
   in a browser while logged in and click through any rules/terms prompt —
   Kaggle sometimes requires accepting a dataset's terms via the web UI once
   before the API will serve it to your account.

Your Kaggle key is a credential like `LLM_API_KEY` — `ai-analytics/.env` is
already gitignored, so it never gets committed alongside `ai-analytics/.env.example`.

## Architecture

```mermaid
flowchart TB
    Browser["🌐 Browser"]

    subgraph docker["Docker network (docker-compose.yaml)"]
        subgraph app["ai-analytics-streamlit container"]
            UI["Streamlit UI<br/>app.py"]
            Agent["AnalyticsAgent<br/>conversation memory"]
            Graph["LangGraph pipeline<br/>src/graph.py"]
            UI --> Agent --> Graph
        end
        subgraph db["clickhouse-local container"]
            CH[("ClickHouse<br/>database: olist<br/>9 tables · ~1.55M rows")]
        end
    end

    LLM["LLM Provider<br/>Gemini / OpenAI / Anthropic<br/>(via LangChain init_chat_model)"]

    Browser <--> UI
    Graph <--> CH
    Graph <--> LLM
```

Nothing outside [`src/llm.py`](ai-analytics/src/llm.py) imports a
provider-specific SDK — switching `LLM_PROVIDER` is a two-variable env change.

### The LangGraph pipeline

```mermaid
flowchart LR
    Start(["User question<br/>+ schema + conversation history"]) --> Gen["generate_sql<br/>(LangChain structured output)"]
    Gen -->|SQL produced| Val["validate_sql<br/>(read-only allowlist)"]
    Gen -->|LLM/API error| Give["give_up"]
    Val -->|valid| Exec["execute_sql<br/>(ClickHouse)"]
    Val -->|invalid, attempts remain| Gen
    Val -->|invalid, max attempts hit| Give
    Exec -->|success| Analyze["analyze_results<br/>(grounded in actual rows)"]
    Exec -->|ClickHouse error, attempts remain| Gen
    Exec -->|ClickHouse error, max attempts hit| Give
    Analyze --> Viz["visualize<br/>(chart type from result shape)"]
    Viz --> End(["Answer + SQL + chart + metadata"])
    Give --> End
```

Every retry re-enters `validate_sql` — a corrected query can never reach
`execute_sql` by skipping the safety gate. Retries are capped by
`MAX_SQL_RETRY_ATTEMPTS` (default 3); see [`tests/test_graph.py`](ai-analytics/tests/test_graph.py)
for tests that pin down exactly this behavior with fake LLM/ClickHouse stubs.

### Conversational follow-ups

```mermaid
sequenceDiagram
    participant U as User
    participant UI as Streamlit UI
    participant A as AnalyticsAgent
    participant G as LangGraph pipeline
    participant CH as ClickHouse
    participant L as LLM

    U->>UI: "Why did revenue decline in July?"
    UI->>A: process_question(question)
    A->>G: invoke(question, schema, conversation_context="")
    G->>L: generate SQL
    L-->>G: SQL + reasoning
    G->>CH: execute SELECT (validated)
    CH-->>G: rows
    G->>L: analyze results
    L-->>G: answer + chart_type
    G-->>A: final state
    A->>A: conversation.add_assistant_message(answer, sql)
    A-->>UI: answer + chart + SQL
    UI-->>U: renders answer, chart, "View SQL" expander

    U->>UI: "Was that caused by fewer customers?"
    UI->>A: process_question(question)
    Note over A: conversation_context now includes<br/>the prior question, answer, and SQL
    A->>G: invoke(question, schema, conversation_context)
    G->>L: generate SQL — resolves "that"<br/>via conversation_context
    Note over L: understands "that" = the July<br/>revenue decline just discussed
```

[`src/conversation.py`](ai-analytics/src/conversation.py) keeps a sliding
window of the last `MAX_CONVERSATION_HISTORY` turns (question, answer, and
the SQL used) — enough for follow-ups like "that", "compare it with 2024",
or "show me that by region" to resolve, without sending the entire chat
history to the LLM on every turn.

## The dataset

[Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce), a
Brazilian e-commerce marketplace dataset: ~99K orders, ~1.55M rows total
across 9 tables.

```mermaid
erDiagram
    CUSTOMERS ||--o{ ORDERS : places
    ORDERS ||--o{ ORDER_ITEMS : contains
    ORDERS ||--o{ ORDER_PAYMENTS : "paid via"
    ORDERS ||--o{ ORDER_REVIEWS : receives
    ORDER_ITEMS }o--|| PRODUCTS : references
    ORDER_ITEMS }o--|| SELLERS : "sold by"
    PRODUCTS }o--|| PRODUCT_CATEGORY_NAME_TRANSLATION : "categorized as (en)"
    CUSTOMERS }o--|| GEOLOCATION : "zip prefix"
    SELLERS }o--|| GEOLOCATION : "zip prefix"

    CUSTOMERS {
        string customer_id PK
        string customer_unique_id
        string customer_zip_code_prefix
        string customer_city
        string customer_state
    }
    ORDERS {
        string order_id PK
        string customer_id FK
        string order_status
        datetime order_purchase_timestamp
    }
    ORDER_ITEMS {
        string order_id FK
        uint32 order_item_id
        string product_id FK
        string seller_id FK
        float64 price
        float64 freight_value
    }
    ORDER_PAYMENTS {
        string order_id FK
        string payment_type
        float64 payment_value
    }
    ORDER_REVIEWS {
        string review_id PK
        string order_id FK
        uint8 review_score
    }
    PRODUCTS {
        string product_id PK
        string product_category_name FK
    }
    SELLERS {
        string seller_id PK
        string seller_zip_code_prefix
    }
    GEOLOCATION {
        string geolocation_zip_code_prefix
        float64 geolocation_lat
        float64 geolocation_lng
    }
    PRODUCT_CATEGORY_NAME_TRANSLATION {
        string product_category_name PK
        string product_category_name_english
    }
```

None of this is hard-coded in the app — [`src/schema_inspector.py`](ai-analytics/src/schema_inspector.py)
discovers tables, columns, types, row counts, and infers these relationships
(via `system.tables`/`system.columns` and shared foreign-key-shaped column
names) at runtime, so the app works against whatever tables actually exist.

## Automatic data provisioning

Two separate steps, deliberately kept apart:

1. **Getting the CSVs onto your machine** — [`scripts/download_dataset.sh`](scripts/download_dataset.sh)
   pulls the dataset from Kaggle via the official `kaggle` CLI, authenticated
   with *your own* Kaggle account, into `data/olist/` (gitignored — nothing
   downloaded here is ever committed to this repo). See
   [Dataset &amp; license](#dataset--license) for why it's fetched this way
   instead of being bundled in the repo.
2. **Loading those CSVs into ClickHouse** — `docker-compose.yaml` mounts
   [`clickhouse/init/01-load-olist-data.sh`](clickhouse/init/01-load-olist-data.sh)
   into `/docker-entrypoint-initdb.d/` and `data/olist/` into `/olist-data/`.
   ClickHouse's official image runs everything in `docker-entrypoint-initdb.d/`
   **only when its data volume is empty** — i.e. the very first time the
   stack starts on a machine. The script creates the `olist` database/tables
   and streams each CSV straight into ClickHouse via `clickhouse-client`. On
   any later `docker compose up` (existing volume, existing data) this step
   is skipped entirely — verified by restarting the container and confirming
   row counts stay exactly the same rather than doubling. **The existing
   ClickHouse data on this machine is never touched, reloaded, or
   overwritten by this mechanism.** If the CSVs aren't there yet (step 1
   skipped), the script fails fast with a message pointing you at
   `download_dataset.sh` instead of a cryptic ClickHouse error.

If you'd rather load data manually against an already-running container,
[`load_data.sh`](load_data.sh) at the repo root does the same thing via
`docker exec` (also reading from `data/olist/`).

## Dataset & license

[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
licensed **CC BY-NC-SA 4.0** (Attribution, NonCommercial, ShareAlike) by
Olist. That license permits non-commercial redistribution with attribution,
but Kaggle-hosted datasets are conventionally *not* re-hosted inside git
repos — instead, `scripts/download_dataset.sh` fetches it through the
official Kaggle API under your own account, so you're pulling the data
directly from its source under your own credentials rather than this repo
redistributing a copy. The data's license is independent of whatever
license the application code carries.

## Features

- **Dynamic schema discovery** — no hard-coded columns; reads `system.tables`/`system.columns` live
- **Schema-aware SQL generation** — LangChain structured output, ClickHouse dialect, joins based on inferred relationships
- **Allowlist SQL validation** — only `SELECT`/`WITH` pass; a fixed set of destructive/DDL keywords is rejected even mid-query; CTEs handled correctly; multi-statement injection blocked
- **Self-correcting execution** — validation/execution errors feed back into the next generation attempt, capped by `MAX_SQL_RETRY_ATTEMPTS`
- **Result analysis grounded in real data** — no invented statistics; distinguishes observed facts from inferences
- **Automatic visualization** — line/bar/horizontal-bar/scatter/pie/metric/table, chosen from the actual result shape, never forced
- **Conversational memory** — sliding-window context resolves "that", "the previous month", "compare it", etc.
- **Full query transparency** — every answer has an expandable "View SQL" panel plus execution time and row count
- **Structured logging** — question, generated SQL, validation result, timings, row counts, errors (no secrets)

## Tech stack

| Layer                 | Choice                                                                  |
| --------------------- | ----------------------------------------------------------------------- |
| UI                    | Streamlit                                                               |
| Orchestration         | LangGraph (`StateGraph`, conditional retry edges)                     |
| LLM interface         | LangChain (`init_chat_model`, structured output) — provider-agnostic |
| Database              | ClickHouse (existing instance, read-only access)                        |
| DB client             | `clickhouse-connect`                                                  |
| Data handling         | pandas                                                                  |
| Charts                | Plotly                                                                  |
| Dependency management | [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`)      |

## Project layout

```
bi-conversation-analytics-ecommerce/
├── docker-compose.yaml        # ClickHouse + ai-analytics, on one Docker network
├── scripts/
│   └── download_dataset.sh    # pulls the dataset from Kaggle (not committed)
├── clickhouse/init/           # runs once, on a fresh ClickHouse volume
│   └── 01-load-olist-data.sh
├── data/olist/                # dataset CSVs land here (gitignored except README.md)
├── load_data.sh               # manual/alternative loader (docker exec-based)
└── ai-analytics/
    ├── app.py                 # Streamlit UI
    ├── pyproject.toml / uv.lock
    ├── Dockerfile
    ├── src/
    │   ├── config.py             # env-driven configuration
    │   ├── clickhouse_client.py  # read-only ClickHouse client + QueryResult
    │   ├── schema_inspector.py   # dynamic schema discovery
    │   ├── llm.py                # LangChain chat-model factory (provider swap point)
    │   ├── sql_generator.py      # LLM SQL generation (structured output)
    │   ├── sql_validator.py      # allowlist-based read-only SQL safety gate
    │   ├── graph.py              # LangGraph StateGraph: pipeline + retries
    │   ├── analytics_agent.py    # orchestrator: conversation + graph invocation
    │   ├── result_analyzer.py    # LLM result interpretation, grounded in data
    │   ├── visualization.py      # Plotly chart selection from result shape
    │   └── conversation.py       # sliding-window conversation memory
    ├── prompts/                  # system prompts for SQL generation / analysis
    └── tests/
        ├── test_sql_validator.py # safety-critical: allowlist behavior
        └── test_graph.py         # pipeline routing/retry behavior (no live LLM needed)
```

See [`ai-analytics/README.md`](ai-analytics/README.md) for app-level dev
setup (running outside Docker, switching LLM providers, running tests).

## Configuration

All configuration is environment-driven via `ai-analytics/.env`
(copy from `.env.example` — never commit `.env`, it's gitignored).

| Variable                                              | Purpose                                                                                    |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `CLICKHOUSE_HOST`                                   | `localhost` on your host machine, `clickhouse` inside the Docker network               |
| `CLICKHOUSE_HTTP_PORT` / `CLICKHOUSE_NATIVE_PORT` | `8123` / `9000`                                                                        |
| `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`         | must match the running ClickHouse container                                                |
| `CLICKHOUSE_DATABASE`                               | `olist`                                                                                  |
| `LLM_PROVIDER`                                      | `gemini` \| `openai` \| `anthropic`                                                  |
| `LLM_API_KEY`                                       | key for the selected provider                                                              |
| `LLM_MODEL`                                         | e.g.`gemini-2.5-flash`, `gpt-4o-mini`, `claude-haiku-4-5`                            |
| `MAX_SQL_RETRY_ATTEMPTS`                            | bounds the generate→validate→execute retry loop (default 3)                              |
| `MAX_CONVERSATION_HISTORY`                          | sliding-window size for follow-up context (default 10)                                     |
| `MAX_RESULT_ROWS_FOR_LLM`                           | result rows sent to the LLM before falling back to head/tail + summary stats (default 200) |

## Safety model

[`src/sql_validator.py`](ai-analytics/src/sql_validator.py) uses an
**allowlist**, not a keyword blocklist:

- Only `SELECT` / `WITH ... SELECT` statements pass.
- `DROP`, `TRUNCATE`, `DELETE`, `ALTER`, `INSERT`, `UPDATE`, `CREATE`,
  `ATTACH`, `DETACH`, `OPTIMIZE`, `GRANT`, `REVOKE`, and more are rejected
  as standalone keywords, even inside otherwise-valid-looking SQL (word-boundary
  matching means `delete_count` as a column alias is fine — only the actual
  keyword is blocked).
- Multiple `;`-separated statements are rejected.
- Every referenced table (CTEs excluded from the check) must exist in the
  schema the inspector actually discovered — no hallucinated tables.

## Running tests

```bash
cd ai-analytics
uv run pytest tests/ -v
```

38 tests: SQL validator safety cases + LangGraph pipeline retry/give-up
behavior driven with fake LLM/ClickHouse stubs (no live services needed).
