"""
LangGraph pipeline for the analytics agent.

Implements the fixed pipeline as an explicit state graph so the
retry-on-error behavior (bad SQL, validation failure, ClickHouse
error) is a graph edge rather than a hand-rolled loop:

    generate_sql -> validate_sql -> execute_sql -> analyze_results -> visualize
         ^               |               |
         '---- retry ----'---- retry ----'
                    (bounded by max_attempts)

Any node can route to `give_up`, which produces a user-facing
message without ever executing unvalidated SQL.
"""

from typing import Optional, TypedDict

import plotly.graph_objects as go
from langgraph.graph import END, StateGraph

from src.clickhouse_client import ClickHouseClient, ClickHouseQueryError, QueryResult
from src.logger import get_logger
from src.result_analyzer import ResultAnalyzer
from src.sql_generator import LLMError, SQLGenerator
from src.sql_validator import SQLValidator
from src.visualization import Visualizer

logger = get_logger(__name__)


class PipelineState(TypedDict, total=False):
    # Inputs
    question: str
    schema_description: str
    conversation_context: str
    max_attempts: int

    # Working state
    attempt: int
    sql: str
    reasoning: str
    error_context: str
    validation_error: str
    execution_error: str
    query_result: Optional[QueryResult]

    # Outputs
    answer: str
    chart_type: str
    chart: Optional[go.Figure]
    failed: bool
    failure_kind: str
    failure_detail: str
    failure_reason: str


def _generate_sql_node(state: PipelineState, generator: SQLGenerator) -> dict:
    attempt = state.get("attempt", 0) + 1
    logger.info("graph_generate_sql", attempt=attempt, max=state["max_attempts"])

    try:
        gen_result = generator.generate_sql(
            question=state["question"],
            schema_description=state["schema_description"],
            conversation_context=state.get("conversation_context", ""),
            error_context=state.get("error_context", ""),
        )
    except LLMError as e:
        return {
            "attempt": attempt,
            "failed": True,
            "failure_kind": "llm_error",
            "failure_detail": str(e),
        }

    raw_sql = gen_result["sql"]
    if not raw_sql:
        return {
            "attempt": attempt,
            "sql": "",
            "error_context": "The LLM did not return a SQL query. Please generate a valid SELECT query.",
        }

    return {
        "attempt": attempt,
        "sql": raw_sql,
        "reasoning": gen_result["reasoning"],
        "error_context": "",
    }


def _route_after_generate(state: PipelineState) -> str:
    if state.get("failed"):
        return "give_up"
    if not state.get("sql"):
        return "give_up" if state["attempt"] >= state["max_attempts"] else "generate_sql"
    return "validate_sql"


def _validate_sql_node(state: PipelineState, get_validator) -> dict:
    validator: SQLValidator = get_validator()
    validation = validator.validate(state["sql"])

    if not validation.is_valid:
        logger.warning("graph_validation_failed", error=validation.error_message)
        return {
            "sql": "",
            "validation_error": validation.error_message,
            "error_context": (
                f"SQL validation failed: {validation.error_message}\n"
                f"Invalid SQL: {state['sql']}\n"
                "Please generate a corrected, read-only SELECT query."
            ),
        }

    return {"sql": validation.cleaned_sql, "validation_error": "", "error_context": ""}


def _route_after_validate(state: PipelineState) -> str:
    if state.get("validation_error"):
        return "give_up" if state["attempt"] >= state["max_attempts"] else "generate_sql"
    return "execute_sql"


def _execute_sql_node(state: PipelineState, ch_client: ClickHouseClient) -> dict:
    try:
        result = ch_client.execute_query(state["sql"])
    except ClickHouseQueryError as e:
        logger.warning("graph_execution_failed", error=str(e))
        return {
            "query_result": None,
            "execution_error": str(e),
            "error_context": (
                f"ClickHouse returned an error: {e}\n"
                f"Failed SQL: {state['sql']}\n"
                "Please fix the SQL and try again."
            ),
        }

    return {"query_result": result, "execution_error": ""}


def _route_after_execute(state: PipelineState) -> str:
    if state.get("execution_error"):
        return "give_up" if state["attempt"] >= state["max_attempts"] else "generate_sql"
    return "analyze_results"


def _analyze_results_node(state: PipelineState, analyzer: ResultAnalyzer) -> dict:
    query_result: QueryResult = state["query_result"]
    analysis = analyzer.analyze(
        question=state["question"],
        sql=state["sql"],
        df=query_result.dataframe,
        conversation_context=state.get("conversation_context", ""),
    )
    return {"answer": analysis["answer"], "chart_type": analysis["chart_suggestion"]}


def _visualize_node(state: PipelineState, visualizer: Visualizer) -> dict:
    query_result: QueryResult = state["query_result"]
    try:
        chart = visualizer.create_chart(
            df=query_result.dataframe,
            chart_type=state.get("chart_type", "none"),
            question=state["question"],
        )
    except Exception as e:
        logger.warning("graph_visualization_failed", error=str(e))
        chart = None
    return {"chart": chart}


def _give_up_node(state: PipelineState) -> dict:
    if state.get("failure_kind") == "llm_error":
        return {
            "answer": "I couldn't generate a query for your question. This may be due to an LLM API issue. Please try again.",
            "failed": True,
            "failure_reason": f"Failed to generate SQL: {state.get('failure_detail', '')}",
        }

    last_error = (
        state.get("error_context")
        or state.get("validation_error")
        or state.get("execution_error")
        or "unknown error"
    )
    return {
        "answer": (
            "I wasn't able to generate a working query for your question. "
            "Could you try rephrasing it or being more specific about what you'd like to know?"
        ),
        "failed": True,
        "failure_reason": f"Failed after {state.get('attempt', 0)} attempts. Last error: {last_error}",
    }


def build_pipeline_graph(
    ch_client: ClickHouseClient,
    sql_generator: SQLGenerator,
    get_validator,
    result_analyzer: ResultAnalyzer,
    visualizer: Visualizer,
):
    """
    Compile the question -> SQL -> validate -> execute -> analyze -> visualize
    pipeline into a runnable LangGraph graph.

    `get_validator` is a callable rather than a `SQLValidator` instance because
    the validator needs the live schema's table names, which are only known
    once the schema inspector has run.
    """
    graph = StateGraph(PipelineState)

    graph.add_node("generate_sql", lambda s: _generate_sql_node(s, sql_generator))
    graph.add_node("validate_sql", lambda s: _validate_sql_node(s, get_validator))
    graph.add_node("execute_sql", lambda s: _execute_sql_node(s, ch_client))
    graph.add_node("analyze_results", lambda s: _analyze_results_node(s, result_analyzer))
    graph.add_node("visualize", lambda s: _visualize_node(s, visualizer))
    graph.add_node("give_up", _give_up_node)

    graph.set_entry_point("generate_sql")

    graph.add_conditional_edges(
        "generate_sql",
        _route_after_generate,
        {"give_up": "give_up", "generate_sql": "generate_sql", "validate_sql": "validate_sql"},
    )
    graph.add_conditional_edges(
        "validate_sql",
        _route_after_validate,
        {"give_up": "give_up", "generate_sql": "generate_sql", "execute_sql": "execute_sql"},
    )
    graph.add_conditional_edges(
        "execute_sql",
        _route_after_execute,
        {"give_up": "give_up", "generate_sql": "generate_sql", "analyze_results": "analyze_results"},
    )

    graph.add_edge("analyze_results", "visualize")
    graph.add_edge("visualize", END)
    graph.add_edge("give_up", END)

    return graph.compile()
