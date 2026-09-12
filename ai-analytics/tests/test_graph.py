"""
Tests for the LangGraph pipeline's routing and retry-bounding logic.

Uses fake generator/validator/client/analyzer/visualizer stubs so the
graph's control flow (generate -> validate -> execute -> analyze,
with bounded retries on any failure) can be verified without a live
LLM or ClickHouse instance.
"""

import pandas as pd
import pytest

from src.clickhouse_client import ClickHouseQueryError, QueryResult
from src.graph import build_pipeline_graph
from src.sql_generator import LLMError
from src.sql_validator import SQLValidator


class FakeGenerator:
    """Returns SQL from a scripted list, one per call, to drive retry scenarios."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate_sql(self, question, schema_description, conversation_context="", error_context=""):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    """Executes SQL from a scripted list of results/exceptions."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def execute_query(self, sql):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeAnalyzer:
    def analyze(self, question, sql, df, conversation_context=""):
        return {"answer": f"Answer for {len(df)} rows", "chart_suggestion": "table"}


class FakeVisualizer:
    def create_chart(self, df, chart_type, question=""):
        return None


def _ok_query_result(rows=1):
    df = pd.DataFrame({"value": list(range(rows))})
    return QueryResult(
        dataframe=df, row_count=rows, column_names=["value"],
        column_types=["Int64"], execution_time_seconds=0.01, sql="SELECT ...",
    )


def _validator():
    return SQLValidator(known_tables={"orders"}, database="olist")


def _build_graph(generator, client, analyzer=None, visualizer=None):
    validator = _validator()
    return build_pipeline_graph(
        ch_client=client,
        sql_generator=generator,
        get_validator=lambda: validator,
        result_analyzer=analyzer or FakeAnalyzer(),
        visualizer=visualizer or FakeVisualizer(),
    )


def _initial_state(max_attempts=3):
    return {
        "question": "How many orders are there?",
        "schema_description": "Table: olist.orders",
        "conversation_context": "",
        "max_attempts": max_attempts,
        "attempt": 0,
    }


class TestHappyPath:
    def test_succeeds_on_first_try(self):
        generator = FakeGenerator([{"sql": "SELECT count() FROM olist.orders", "reasoning": "count"}])
        client = FakeClient([_ok_query_result(5)])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state())

        assert not final.get("failed")
        assert final["attempt"] == 1
        assert generator.calls == 1
        assert client.calls == 1
        assert "Answer for 5 rows" in final["answer"]


class TestRetryOnValidationFailure:
    def test_retries_then_succeeds(self):
        generator = FakeGenerator([
            {"sql": "DROP TABLE olist.orders", "reasoning": "bad"},
            {"sql": "SELECT count() FROM olist.orders", "reasoning": "fixed"},
        ])
        client = FakeClient([_ok_query_result(1)])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state())

        assert not final.get("failed")
        assert generator.calls == 2
        assert client.calls == 1

    def test_gives_up_after_max_attempts(self):
        generator = FakeGenerator([
            {"sql": "DROP TABLE olist.orders", "reasoning": "bad"},
            {"sql": "DROP TABLE olist.orders", "reasoning": "still bad"},
        ])
        client = FakeClient([])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state(max_attempts=2))

        assert final["failed"]
        assert generator.calls == 2
        assert client.calls == 0
        assert "Failed after 2 attempts" in final["failure_reason"]


class TestRetryOnExecutionFailure:
    def test_retries_then_succeeds(self):
        generator = FakeGenerator([
            {"sql": "SELECT count() FROM olist.orders", "reasoning": "first try"},
            {"sql": "SELECT count() FROM olist.orders", "reasoning": "second try"},
        ])
        client = FakeClient([
            ClickHouseQueryError("Unknown identifier", sql="SELECT count() FROM olist.orders"),
            _ok_query_result(1),
        ])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state())

        assert not final.get("failed")
        assert generator.calls == 2
        assert client.calls == 2

    def test_never_bypasses_validation_on_retry(self):
        """A syntactically-invalid SQL retried after an execution error must still
        go through validate_sql — the retry loop can't skip the safety gate."""
        generator = FakeGenerator([
            {"sql": "SELECT count() FROM olist.orders", "reasoning": "first try"},
            {"sql": "DROP TABLE olist.orders", "reasoning": "hallucinated after error"},
        ])
        client = FakeClient([
            ClickHouseQueryError("Unknown identifier", sql="SELECT count() FROM olist.orders"),
        ])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state(max_attempts=2))

        assert final["failed"]
        assert client.calls == 1  # the DROP was never executed


class TestLLMFailure:
    def test_llm_error_gives_up_immediately_without_retry(self):
        generator = FakeGenerator([LLMError("API key invalid")])
        client = FakeClient([])
        graph = _build_graph(generator, client)

        final = graph.invoke(_initial_state(max_attempts=3))

        assert final["failed"]
        assert generator.calls == 1  # no retries for infra-level LLM errors
        assert "API key invalid" in final["failure_reason"]
