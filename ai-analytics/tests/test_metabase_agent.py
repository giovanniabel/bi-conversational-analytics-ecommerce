"""
Tests for the Metabase agent's tools.

The LLM itself isn't exercised here — what matters is that the tools the
model is handed behave safely regardless of what it asks for: writes are
rejected before reaching Metabase, and only real mutations get reported
to the UI as actions.
"""

import json

import pytest

from src.metabase_agent import MetabaseAgent
from src.metabase_client import MetabaseError
from src.sql_validator import SQLValidator

KNOWN_TABLES = {"orders", "order_items", "sellers", "products", "order_reviews"}


class FakeMetabase:
    """Records calls so tests can assert what did (and didn't) reach Metabase."""

    def __init__(self, fail_on=None):
        self.queries: list[str] = []
        self.created_cards: list[dict] = []
        self.pinned: list[tuple[int, int]] = []
        self.created_dashboards: list[str] = []
        self._fail_on = fail_on or set()

    def run_native_query(self, database_id, sql):
        if "query" in self._fail_on:
            raise MetabaseError("boom")
        self.queries.append(sql)
        return {"columns": ["n"], "rows": [[1], [2], [3]], "row_count": 3}

    def create_native_card(self, name, sql, database_id, display="table", description=""):
        card = {"id": 100 + len(self.created_cards), "name": name, "display": display, "sql": sql}
        self.created_cards.append(card)
        return card

    def add_card_to_dashboard(self, dashboard_id, card_id):
        self.pinned.append((dashboard_id, card_id))
        return {"id": dashboard_id}

    def create_dashboard(self, name, description=""):
        self.created_dashboards.append(name)
        return {"id": 500, "name": name}

    def enable_dashboard_embedding(self, dashboard_id):
        pass

    def list_dashboards(self):
        return []

    def list_cards(self):
        return []


class FakeSchemaInspector:
    def get_schema_description(self):
        return "olist.orders(order_id, order_status)"


def build_agent(client=None, default_dashboard_id=7, monkeypatch=None):
    """
    Construct an agent without touching the LLM.

    create_react_agent would instantiate a chat model (and demand an API
    key), so __init__ is bypassed and only the tool-facing state is set up.
    """
    agent = object.__new__(MetabaseAgent)
    agent._client = client or FakeMetabase()
    agent._database_id = 1
    agent._default_dashboard_id = default_dashboard_id
    agent._schema_inspector = FakeSchemaInspector()
    agent._validator = SQLValidator(known_tables=KNOWN_TABLES, database="olist")
    agent._actions = []
    agent._queries = []
    return agent


def tools_by_name(agent):
    return {t.name: t for t in agent._build_tools()}


class TestQueryDataSafety:
    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE olist.orders",
            "DELETE FROM olist.orders",
            "INSERT INTO olist.orders VALUES (1)",
            "UPDATE olist.orders SET order_status = 'x'",
            "SELECT 1; DROP TABLE olist.orders",
        ],
    )
    def test_write_statements_never_reach_metabase(self, sql):
        client = FakeMetabase()
        agent = build_agent(client)

        result = tools_by_name(agent)["query_data"].invoke({"sql": sql})

        assert result.startswith("REJECTED")
        assert client.queries == [], "a rejected query must not be executed"

    def test_select_is_executed_and_returned_as_json(self):
        client = FakeMetabase()
        agent = build_agent(client)

        result = tools_by_name(agent)["query_data"].invoke(
            {"sql": "SELECT count() FROM olist.orders"}
        )

        payload = json.loads(result)
        assert payload["row_count"] == 3
        assert payload["columns"] == ["n"]
        assert len(client.queries) == 1

    def test_query_failure_is_reported_not_raised(self):
        agent = build_agent(FakeMetabase(fail_on={"query"}))

        result = tools_by_name(agent)["query_data"].invoke(
            {"sql": "SELECT bad FROM olist.orders"}
        )

        assert "Query failed" in result

    def test_successful_query_is_recorded_for_the_ui(self):
        agent = build_agent()

        tools_by_name(agent)["query_data"].invoke({"sql": "SELECT count() FROM olist.orders"})

        assert agent._queries == ["SELECT count() FROM olist.orders"]

    def test_rejected_query_is_not_recorded(self):
        agent = build_agent()

        tools_by_name(agent)["query_data"].invoke({"sql": "DROP TABLE olist.orders"})

        assert agent._queries == []


class TestSaveQuestion:
    def test_rejects_non_select_sql(self):
        client = FakeMetabase()
        agent = build_agent(client)

        result = tools_by_name(agent)["save_question"].invoke(
            {"name": "Sneaky", "sql": "DROP TABLE olist.orders"}
        )

        assert result.startswith("REJECTED")
        assert client.created_cards == []
        assert agent._actions == []

    def test_creates_card_and_records_action(self):
        client = FakeMetabase()
        agent = build_agent(client)

        result = tools_by_name(agent)["save_question"].invoke(
            {"name": "Orders", "sql": "SELECT count() FROM olist.orders", "chart_type": "scalar"}
        )

        assert "Saved as question" in result
        assert len(client.created_cards) == 1
        assert client.created_cards[0]["display"] == "scalar"
        assert agent._actions[0].kind == "saved_question"

    def test_unknown_chart_type_falls_back_to_table(self):
        client = FakeMetabase()
        agent = build_agent(client)

        tools_by_name(agent)["save_question"].invoke(
            {"name": "X", "sql": "SELECT 1 FROM olist.orders", "chart_type": "hologram"}
        )

        assert client.created_cards[0]["display"] == "table"


class TestPinToDashboard:
    def test_defaults_to_main_dashboard(self):
        client = FakeMetabase()
        agent = build_agent(client, default_dashboard_id=7)

        tools_by_name(agent)["pin_to_dashboard"].invoke({"card_id": 42})

        assert client.pinned == [(7, 42)]

    def test_explicit_dashboard_wins(self):
        client = FakeMetabase()
        agent = build_agent(client, default_dashboard_id=7)

        tools_by_name(agent)["pin_to_dashboard"].invoke({"card_id": 42, "dashboard_id": 9})

        assert client.pinned == [(9, 42)]

    def test_without_any_dashboard_it_asks_rather_than_failing(self):
        client = FakeMetabase()
        agent = build_agent(client, default_dashboard_id=None)

        result = tools_by_name(agent)["pin_to_dashboard"].invoke({"card_id": 42})

        assert "create_dashboard" in result
        assert client.pinned == []


class TestCreateDashboard:
    def test_records_action_with_browser_url(self):
        client = FakeMetabase()
        agent = build_agent(client)

        tools_by_name(agent)["create_dashboard"].invoke({"name": "Ops"})

        assert client.created_dashboards == ["Ops"]
        action = agent._actions[0]
        assert action.kind == "created_dashboard"
        # The URL is what the UI links to, so it must be browser-reachable.
        assert action.url.startswith("http")
        assert "/dashboard/500" in action.url
