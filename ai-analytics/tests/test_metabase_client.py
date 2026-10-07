"""
Tests for the Metabase API client's local logic.

The HTTP layer is stubbed out — what's worth pinning down here is the
behavior *around* the calls: that adding a dashboard card preserves the
cards already there (getting this wrong silently wipes a dashboard), and
that query results are normalized consistently.
"""

import pytest

from src.metabase_client import MetabaseClient, MetabaseError


class FakeClient(MetabaseClient):
    """MetabaseClient with `_request` replaced by a scripted fake."""

    def __init__(self, responses=None):
        super().__init__(internal_url="http://metabase:3000", username="u", password="p")
        self._session_token = "fake-token"
        self.calls: list[tuple[str, str, object]] = []
        self._responses = responses or {}

    def _request(self, method, path, *, json=None, authed=True, retry_auth=True):
        self.calls.append((method, path, json))
        key = f"{method} {path}"
        value = self._responses.get(key)
        if callable(value):
            return value(json)
        return value


class TestAddCardToDashboard:
    def test_preserves_existing_cards(self):
        existing = [
            {"id": 1, "card_id": 10, "row": 0, "col": 0, "size_x": 12, "size_y": 4},
            {"id": 2, "card_id": 11, "row": 4, "col": 0, "size_x": 12, "size_y": 6},
        ]
        client = FakeClient({
            "GET /api/dashboard/7": {"id": 7, "dashcards": existing},
            "PUT /api/dashboard/7": {"id": 7},
        })

        client.add_card_to_dashboard(dashboard_id=7, card_id=99)

        put = next(c for c in client.calls if c[0] == "PUT")
        dashcards = put[2]["dashcards"]
        assert len(dashcards) == 3, "existing cards must survive the update"
        assert [d["card_id"] for d in dashcards[:2]] == [10, 11]

    def test_stacks_new_card_below_existing(self):
        existing = [
            {"id": 1, "card_id": 10, "row": 0, "col": 0, "size_x": 12, "size_y": 4},
            {"id": 2, "card_id": 11, "row": 4, "col": 0, "size_x": 12, "size_y": 6},
        ]
        client = FakeClient({
            "GET /api/dashboard/7": {"id": 7, "dashcards": existing},
            "PUT /api/dashboard/7": {"id": 7},
        })

        client.add_card_to_dashboard(dashboard_id=7, card_id=99)

        new_card = next(c for c in client.calls if c[0] == "PUT")[2]["dashcards"][-1]
        # Lowest free row is 4 + 6 = 10; anything less would overlap.
        assert new_card["row"] == 10
        assert new_card["card_id"] == 99
        assert new_card["id"] == -1, "negative id marks the dashcard as new"

    def test_first_card_starts_at_top(self):
        client = FakeClient({
            "GET /api/dashboard/7": {"id": 7, "dashcards": []},
            "PUT /api/dashboard/7": {"id": 7},
        })

        client.add_card_to_dashboard(dashboard_id=7, card_id=99)

        new_card = next(c for c in client.calls if c[0] == "PUT")[2]["dashcards"][0]
        assert new_card["row"] == 0

    def test_handles_dashboard_with_null_dashcards(self):
        client = FakeClient({
            "GET /api/dashboard/7": {"id": 7, "dashcards": None},
            "PUT /api/dashboard/7": {"id": 7},
        })

        client.add_card_to_dashboard(dashboard_id=7, card_id=99)

        dashcards = next(c for c in client.calls if c[0] == "PUT")[2]["dashcards"]
        assert len(dashcards) == 1


class TestRunNativeQuery:
    def test_normalizes_columns_and_rows(self):
        client = FakeClient({
            "POST /api/dataset": {
                "status": "completed",
                "data": {
                    "cols": [{"name": "city", "display_name": "City"}, {"name": "orders"}],
                    "rows": [["sao paulo", 10], ["curitiba", 5]],
                },
            }
        })

        result = client.run_native_query(1, "SELECT 1")

        assert result["columns"] == ["City", "orders"]
        assert result["rows"] == [["sao paulo", 10], ["curitiba", 5]]
        assert result["row_count"] == 2

    def test_raises_on_failed_query(self):
        client = FakeClient({
            "POST /api/dataset": {"status": "failed", "error": "Unknown column xyz"}
        })

        with pytest.raises(MetabaseError, match="Unknown column xyz"):
            client.run_native_query(1, "SELECT xyz FROM olist.orders")


class TestListing:
    def test_list_cards_skips_archived(self):
        client = FakeClient({
            "GET /api/card": [
                {"id": 1, "name": "Live", "display": "bar"},
                {"id": 2, "name": "Old", "display": "table", "archived": True},
            ]
        })

        cards = client.list_cards()

        assert [c.id for c in cards] == [1]

    def test_list_databases_handles_both_response_shapes(self):
        wrapped = FakeClient({"GET /api/database": {"data": [{"id": 1, "name": "CH"}]}})
        bare = FakeClient({"GET /api/database": [{"id": 1, "name": "CH"}]})

        assert wrapped.list_databases() == [{"id": 1, "name": "CH"}]
        assert bare.list_databases() == [{"id": 1, "name": "CH"}]

    def test_find_database_by_name_returns_none_when_absent(self):
        client = FakeClient({"GET /api/database": {"data": [{"id": 1, "name": "Other"}]}})

        assert client.find_database_by_name("Olist ClickHouse") is None
