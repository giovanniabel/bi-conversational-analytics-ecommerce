"""
Claude <-> Metabase conversational agent.

A LangGraph tool-calling agent that can read and *build* Metabase content:
it answers questions by querying ClickHouse through Metabase, and can
persist an answer as a saved question pinned to a dashboard.

Every SQL string the model produces passes through the same read-only
SQLValidator the Streamlit pipeline uses, so the model cannot write to
ClickHouse even though Metabase's connection user could.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.config import metabase_config
from src.llm import get_chat_model
from src.logger import get_logger
from src.metabase_client import MetabaseClient, MetabaseError
from src.schema_inspector import SchemaInspector
from src.sql_validator import SQLValidator

logger = get_logger(__name__)

# Metabase's `display` values that make sense for a generated question.
ChartType = Literal["table", "bar", "row", "line", "area", "pie", "scalar", "scatter"]
VALID_DISPLAYS = {"table", "bar", "row", "line", "area", "pie", "scalar", "scatter"}

MAX_ROWS_RETURNED_TO_MODEL = 50

SYSTEM_PROMPT = """You are an analytics assistant for a Brazilian e-commerce \
business, working on top of a Metabase instance connected to a ClickHouse \
database (the Olist public dataset).

You can query the data and you can BUILD things in Metabase — saved questions \
and dashboards that persist for the whole team.

How to work:
- Call `describe_tables` before writing SQL the first time, so you use real \
column names rather than guessing.
- Use `query_data` to actually run SQL and answer from the rows you get back. \
Never invent numbers; if you did not run a query, say so.
- Queries are ClickHouse SQL. Tables live in the `olist` database, so \
fully-qualify them (e.g. `olist.orders`). Only read-only SELECT/WITH queries \
are permitted — anything else is rejected before it reaches the database.
- When the user asks to save, pin, track, or "add to the dashboard", use \
`save_question` and then `pin_to_dashboard`. Pick a `chart_type` that fits \
the shape of the result: `scalar` for a single number, `line` for a time \
series, `row` for ranked categories, `bar` for category comparisons, `pie` \
for parts of a whole, `table` when nothing else fits.
- Do not save a question unless the user asked for something to be saved or \
added to a dashboard. Answering is not saving.

Style: answer like a colleague briefing a non-technical teammate. Lead with \
the finding and the numbers that support it. Keep it to a few sentences unless \
asked for more. Mention the SQL only if asked."""


@dataclass
class AgentAction:
    """A mutating thing the agent did, surfaced to the UI."""
    kind: str  # "saved_question" | "created_dashboard" | "pinned_card"
    id: int
    name: str
    url: str = ""


@dataclass
class AgentReply:
    answer: str
    actions: list[AgentAction] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    error: str = ""


class MetabaseAgent:
    """Conversational agent with tools bound to a live Metabase instance."""

    def __init__(
        self,
        client: MetabaseClient,
        database_id: int,
        default_dashboard_id: Optional[int],
        schema_inspector: SchemaInspector,
        validator: SQLValidator,
    ):
        self._client = client
        self._database_id = database_id
        self._default_dashboard_id = default_dashboard_id
        self._schema_inspector = schema_inspector
        self._validator = validator

        # Per-turn scratch state, reset at the start of every chat() call.
        self._actions: list[AgentAction] = []
        self._queries: list[str] = []

        self._agent = create_react_agent(
            model=get_chat_model(temperature=0.0),
            tools=self._build_tools(),
            prompt=SYSTEM_PROMPT,
        )

    # ── tools ─────────────────────────────────────────────────────────────

    def _build_tools(self) -> list:
        client = self._client
        database_id = self._database_id

        @tool
        def describe_tables() -> str:
            """Describe the available tables and their columns. Call this before writing SQL."""
            try:
                return self._schema_inspector.get_schema_description()
            except Exception as e:
                return f"Could not read the schema: {e}"

        @tool
        def query_data(sql: str) -> str:
            """
            Run a read-only ClickHouse SELECT query through Metabase and return the rows.

            Args:
                sql: A SELECT (or WITH ... SELECT) query. Fully-qualify tables, e.g. olist.orders.
            """
            validation = self._validator.validate(sql)
            if not validation.is_valid:
                return (
                    f"REJECTED: {validation.error_message}. "
                    "Rewrite it as a read-only SELECT over the olist tables."
                )

            try:
                result = client.run_native_query(database_id, validation.cleaned_sql)
            except MetabaseError as e:
                return f"Query failed: {e}. Check column names with describe_tables and retry."

            self._queries.append(validation.cleaned_sql)

            rows = result["rows"]
            truncated = len(rows) > MAX_ROWS_RETURNED_TO_MODEL
            shown = rows[:MAX_ROWS_RETURNED_TO_MODEL]
            payload = {
                "columns": result["columns"],
                "rows": shown,
                "row_count": result["row_count"],
            }
            if truncated:
                payload["note"] = (
                    f"Showing first {MAX_ROWS_RETURNED_TO_MODEL} of {result['row_count']} rows."
                )
            return json.dumps(payload, default=str)

        @tool
        def list_dashboards() -> str:
            """List the dashboards that exist in Metabase."""
            try:
                dashboards = client.list_dashboards()
            except MetabaseError as e:
                return f"Could not list dashboards: {e}"
            if not dashboards:
                return "No dashboards exist yet."
            return json.dumps(
                [{"id": d.id, "name": d.name, "description": d.description} for d in dashboards]
            )

        @tool
        def list_saved_questions() -> str:
            """List the saved questions (cards) that exist in Metabase."""
            try:
                cards = client.list_cards()
            except MetabaseError as e:
                return f"Could not list saved questions: {e}"
            if not cards:
                return "No saved questions exist yet."
            return json.dumps(
                [{"id": c.id, "name": c.name, "chart_type": c.display} for c in cards]
            )

        @tool
        def save_question(
            name: str,
            sql: str,
            chart_type: str = "table",
            description: str = "",
        ) -> str:
            """
            Save a SQL query as a reusable Metabase question (card).

            Args:
                name: Short human-readable title, e.g. "Revenue by Month".
                sql: The read-only SELECT query powering it.
                chart_type: One of table, bar, row, line, area, pie, scalar, scatter.
                description: Optional one-line explanation.
            """
            validation = self._validator.validate(sql)
            if not validation.is_valid:
                return f"REJECTED: {validation.error_message}"

            display = chart_type if chart_type in VALID_DISPLAYS else "table"

            try:
                card = client.create_native_card(
                    name=name,
                    sql=validation.cleaned_sql,
                    database_id=database_id,
                    display=display,
                    description=description,
                )
            except MetabaseError as e:
                return f"Could not save the question: {e}"

            card_id = card["id"]
            self._actions.append(
                AgentAction(
                    kind="saved_question",
                    id=card_id,
                    name=name,
                    url=f"{metabase_config.public_url}/question/{card_id}",
                )
            )
            return f"Saved as question #{card_id} ('{name}', {display} chart)."

        @tool
        def create_dashboard(name: str, description: str = "") -> str:
            """
            Create a new, empty Metabase dashboard.

            Args:
                name: Dashboard title.
                description: Optional one-line explanation.
            """
            try:
                dashboard = client.create_dashboard(name=name, description=description)
            except MetabaseError as e:
                return f"Could not create the dashboard: {e}"

            dashboard_id = dashboard["id"]
            try:
                client.enable_dashboard_embedding(dashboard_id)
            except MetabaseError:
                pass

            self._actions.append(
                AgentAction(
                    kind="created_dashboard",
                    id=dashboard_id,
                    name=name,
                    url=f"{metabase_config.public_url}/dashboard/{dashboard_id}",
                )
            )
            return f"Created dashboard #{dashboard_id} ('{name}')."

        @tool
        def pin_to_dashboard(card_id: int, dashboard_id: int = 0) -> str:
            """
            Add an existing saved question to a dashboard.

            Args:
                card_id: The question's id, as returned by save_question.
                dashboard_id: Target dashboard. Omit or pass 0 for the main dashboard.
            """
            target = dashboard_id or self._default_dashboard_id
            if not target:
                return "No dashboard available — create one first with create_dashboard."

            try:
                client.add_card_to_dashboard(dashboard_id=target, card_id=card_id)
            except MetabaseError as e:
                return f"Could not pin the question: {e}"

            self._actions.append(
                AgentAction(
                    kind="pinned_card",
                    id=target,
                    name=f"card {card_id} -> dashboard {target}",
                    url=f"{metabase_config.public_url}/dashboard/{target}",
                )
            )
            return f"Pinned question #{card_id} to dashboard #{target}."

        return [
            describe_tables,
            query_data,
            list_dashboards,
            list_saved_questions,
            save_question,
            create_dashboard,
            pin_to_dashboard,
        ]

    # ── conversation ──────────────────────────────────────────────────────

    def chat(self, message: str, history: Optional[list[dict[str, str]]] = None) -> AgentReply:
        """
        Run one conversational turn.

        Args:
            message: The user's message.
            history: Prior turns as [{"role": "user"|"assistant", "content": ...}].
        """
        self._actions = []
        self._queries = []

        messages: list[Any] = []
        for turn in history or []:
            if turn.get("role") == "user":
                messages.append(HumanMessage(content=turn.get("content", "")))
            elif turn.get("role") == "assistant":
                messages.append(AIMessage(content=turn.get("content", "")))
        messages.append(HumanMessage(content=message))

        try:
            result = self._agent.invoke(
                {"messages": messages},
                config={"recursion_limit": 30},
            )
        except Exception as e:
            logger.error("metabase_agent_failed", error=str(e))
            return AgentReply(
                answer="I hit an error talking to the model. Please try again.",
                error=str(e),
            )

        answer = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
                answer = msg.content.strip()
                break
            # Some providers return content as a list of blocks.
            if isinstance(msg, AIMessage) and isinstance(msg.content, list):
                text = "".join(
                    part.get("text", "")
                    for part in msg.content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
                if text:
                    answer = text
                    break

        if not answer:
            answer = "I wasn't able to produce an answer for that. Could you rephrase?"

        logger.info(
            "metabase_agent_turn",
            actions=len(self._actions),
            queries=len(self._queries),
        )
        return AgentReply(answer=answer, actions=list(self._actions), queries_run=list(self._queries))
