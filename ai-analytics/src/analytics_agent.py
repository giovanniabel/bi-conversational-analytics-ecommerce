"""
Analytics Agent — the main orchestrator.

Wraps the LangGraph pipeline (schema -> SQL generation -> validation
-> execution -> result analysis -> visualization) with conversation
memory and produces a UI-ready AnalyticsResponse.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from src.clickhouse_client import ClickHouseClient
from src.config import app_config
from src.conversation import ConversationManager
from src.graph import PipelineState, build_pipeline_graph
from src.logger import get_logger
from src.result_analyzer import ResultAnalyzer
from src.schema_inspector import SchemaInspector
from src.sql_generator import SQLGenerator
from src.sql_validator import SQLValidator
from src.visualization import Visualizer

logger = get_logger(__name__)


@dataclass
class AnalyticsResponse:
    """Complete response from the analytics pipeline."""
    answer: str
    sql: str = ""
    dataframe: Optional[pd.DataFrame] = None
    chart: Optional[go.Figure] = None
    chart_type: str = "none"
    row_count: int = 0
    execution_time: float = 0.0
    reasoning: str = ""
    error: str = ""
    attempts: int = 0


class AnalyticsAgent:
    """
    Orchestrates the full analytics pipeline via a compiled LangGraph graph.

    Responsibilities kept outside the graph:
    - Conversation memory (so follow-up questions carry context)
    - Schema retrieval and the schema-aware SQL validator's table allowlist
    - Translating the final graph state into a UI-ready AnalyticsResponse
    """

    def __init__(
        self,
        ch_client: ClickHouseClient,
        schema_inspector: SchemaInspector,
        sql_generator: SQLGenerator,
        result_analyzer: ResultAnalyzer,
        visualizer: Visualizer,
        conversation: ConversationManager,
    ):
        self._ch_client = ch_client
        self._schema_inspector = schema_inspector
        self._sql_generator = sql_generator
        self._result_analyzer = result_analyzer
        self._visualizer = visualizer
        self._conversation = conversation
        self._sql_validator: Optional[SQLValidator] = None
        self._graph = None

    def _get_validator(self) -> SQLValidator:
        """Get or create the SQL validator with known tables."""
        if self._sql_validator is None:
            schema = self._schema_inspector.get_schema()
            known_tables = set(schema.tables.keys())
            self._sql_validator = SQLValidator(
                known_tables=known_tables,
                database=schema.database,
            )
        return self._sql_validator

    def _get_graph(self):
        """Lazily compile the LangGraph pipeline (needs the validator's table allowlist)."""
        if self._graph is None:
            self._graph = build_pipeline_graph(
                ch_client=self._ch_client,
                sql_generator=self._sql_generator,
                get_validator=self._get_validator,
                result_analyzer=self._result_analyzer,
                visualizer=self._visualizer,
            )
        return self._graph

    def process_question(self, question: str) -> AnalyticsResponse:
        """
        Process a natural-language question through the full pipeline.

        Args:
            question: The user's question in natural language.

        Returns:
            AnalyticsResponse with answer, SQL, data, chart, and metadata.
        """
        logger.info("processing_question", question=question[:100])

        self._conversation.add_user_message(question)

        try:
            schema_description = self._schema_inspector.get_schema_description()
        except Exception as e:
            error_msg = f"Failed to retrieve database schema: {str(e)}"
            logger.error("schema_retrieval_failed", error=str(e))
            return AnalyticsResponse(
                answer="I'm unable to connect to the database. Please check that ClickHouse is running.",
                error=error_msg,
            )

        conversation_context = self._conversation.get_context_string()

        initial_state: PipelineState = {
            "question": question,
            "schema_description": schema_description,
            "conversation_context": conversation_context,
            "max_attempts": app_config.max_sql_retry_attempts,
            "attempt": 0,
        }

        graph = self._get_graph()
        final_state: PipelineState = graph.invoke(initial_state, config={"recursion_limit": 50})

        if final_state.get("failed"):
            error_msg = final_state.get("failure_reason", "Unknown pipeline failure")
            logger.error("pipeline_failed", error=error_msg)
            self._conversation.add_assistant_message(
                final_state["answer"], sql=final_state.get("sql", "")
            )
            return AnalyticsResponse(
                answer=final_state["answer"],
                sql=final_state.get("sql", ""),
                error=error_msg,
                attempts=final_state.get("attempt", 0),
            )

        query_result = final_state["query_result"]
        answer = final_state["answer"]

        self._conversation.add_assistant_message(answer, sql=final_state["sql"])

        response = AnalyticsResponse(
            answer=answer,
            sql=final_state["sql"],
            dataframe=query_result.dataframe,
            chart=final_state.get("chart"),
            chart_type=final_state.get("chart_type", "none"),
            row_count=query_result.row_count,
            execution_time=query_result.execution_time_seconds,
            reasoning=final_state.get("reasoning", ""),
            attempts=final_state.get("attempt", 0),
        )

        logger.info(
            "question_processed",
            row_count=response.row_count,
            execution_time=response.execution_time,
            chart_type=response.chart_type,
            attempts=response.attempts,
        )

        return response

    def get_suggested_questions(self) -> list[str]:
        """Get schema-aware suggested questions."""
        return self._schema_inspector.generate_suggested_questions()

    def clear_conversation(self) -> None:
        """Clear conversation history."""
        self._conversation.clear()
