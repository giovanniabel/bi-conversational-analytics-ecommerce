"""
Metabase REST API client.

Wraps the subset of Metabase's API this project needs: session auth,
database registration, running native queries, and creating//updating
cards and dashboards.

Two URLs matter and they are not interchangeable:
- `internal_url` is how this process reaches Metabase (service name on the
  Docker network, e.g. http://metabase:3000).
- `public_url` is how the *browser* reaches it, which is what iframe `src`
  attributes must use (e.g. http://localhost:3000).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from src.logger import get_logger

logger = get_logger(__name__)


class MetabaseError(Exception):
    """Raised when a Metabase API call fails."""


@dataclass
class CardSummary:
    id: int
    name: str
    description: str = ""
    display: str = "table"


@dataclass
class DashboardSummary:
    id: int
    name: str
    description: str = ""


class MetabaseClient:
    """Thin, synchronous Metabase API client."""

    def __init__(
        self,
        internal_url: str,
        username: str,
        password: str,
        timeout: float = 60.0,
    ):
        self._base = internal_url.rstrip("/")
        self._username = username
        self._password = password
        self._session_token: Optional[str] = None
        self._http = httpx.Client(timeout=timeout)

    # ── plumbing ──────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._session_token:
            headers["X-Metabase-Session"] = self._session_token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        authed: bool = True,
        retry_auth: bool = True,
    ) -> Any:
        if authed and not self._session_token:
            self.authenticate()

        url = f"{self._base}{path}"
        response = self._http.request(method, url, json=json, headers=self._headers())

        # A stale session token reads as 401; re-auth once and replay.
        if response.status_code == 401 and authed and retry_auth:
            logger.info("metabase_session_expired_reauthenticating")
            self._session_token = None
            self.authenticate()
            return self._request(method, path, json=json, authed=authed, retry_auth=False)

        if response.status_code >= 400:
            raise MetabaseError(
                f"{method} {path} failed [{response.status_code}]: {response.text[:500]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # ── auth / health ─────────────────────────────────────────────────────

    def wait_until_ready(self, timeout_seconds: float = 300.0, interval: float = 3.0) -> None:
        """Block until Metabase answers its health endpoint."""
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = self._http.get(f"{self._base}/api/health", timeout=10.0)
                if response.status_code == 200:
                    logger.info("metabase_ready")
                    return
                last_error = f"status {response.status_code}"
            except httpx.HTTPError as e:
                last_error = str(e)
            time.sleep(interval)
        raise MetabaseError(f"Metabase not ready after {timeout_seconds}s: {last_error}")

    def session_properties(self) -> dict:
        """Public properties — includes `has-user-setup` and the setup token."""
        return self._request("GET", "/api/session/properties", authed=False)

    def has_user_setup(self) -> bool:
        return bool(self.session_properties().get("has-user-setup"))

    def authenticate(self) -> str:
        """Exchange username/password for a session token."""
        url = f"{self._base}/api/session"
        response = self._http.post(
            url,
            json={"username": self._username, "password": self._password},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            raise MetabaseError(
                f"Metabase login failed for {self._username} "
                f"[{response.status_code}]: {response.text[:300]}"
            )
        token = response.json().get("id")
        if not token:
            raise MetabaseError("Metabase login returned no session id")
        self._session_token = token
        logger.info("metabase_authenticated", user=self._username)
        return token

    def run_setup(self, email: str, password: str, site_name: str) -> str:
        """
        Complete the first-run setup wizard, creating the admin user.

        Only valid on a brand-new Metabase instance; the setup token is
        single-use and disappears once a user exists.
        """
        properties = self.session_properties()
        setup_token = properties.get("setup-token")
        if not setup_token:
            raise MetabaseError(
                "No setup-token available — Metabase has already been initialized."
            )

        payload = {
            "token": setup_token,
            "user": {
                "first_name": "Analytics",
                "last_name": "Admin",
                "email": email,
                "password": password,
                "site_name": site_name,
            },
            "prefs": {"site_name": site_name, "allow_tracking": False},
            "database": None,
        }
        result = self._request("POST", "/api/setup", json=payload, authed=False)
        token = (result or {}).get("id")
        if token:
            self._session_token = token
        logger.info("metabase_setup_completed", email=email)
        return token or ""

    # ── settings ──────────────────────────────────────────────────────────

    def set_setting(self, key: str, value: Any) -> None:
        self._request("PUT", f"/api/setting/{key}", json={"value": value})

    def get_setting(self, key: str) -> Any:
        """Read a single admin setting. Returns None if it isn't readable."""
        try:
            return self._request("GET", f"/api/setting/{key}")
        except MetabaseError as e:
            logger.info("metabase_setting_unreadable", setting=key, reason=str(e)[:120])
            return None

    # ── databases ─────────────────────────────────────────────────────────

    def list_databases(self) -> list[dict]:
        result = self._request("GET", "/api/database")
        # Metabase returns either a bare list (older) or {"data": [...]}.
        if isinstance(result, dict):
            return result.get("data", [])
        return result or []

    def find_database_by_name(self, name: str) -> Optional[dict]:
        for db in self.list_databases():
            if db.get("name") == name:
                return db
        return None

    def create_clickhouse_database(
        self,
        name: str,
        host: str,
        port: int,
        user: str,
        password: str,
        dbname: str,
    ) -> dict:
        payload = {
            "name": name,
            "engine": "clickhouse",
            "details": {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "dbname": dbname,
                "ssl": False,
                "tunnel-enabled": False,
            },
            "is_full_sync": True,
        }
        db = self._request("POST", "/api/database", json=payload)
        logger.info("metabase_database_created", name=name, id=db.get("id"))
        return db

    def sync_database_schema(self, database_id: int) -> None:
        self._request("POST", f"/api/database/{database_id}/sync_schema")

    def database_field_count(self, database_id: int) -> int:
        """How many fields Metabase has discovered — 0 means sync hasn't landed."""
        try:
            result = self._request("GET", f"/api/database/{database_id}/fields")
            return len(result or [])
        except MetabaseError:
            return 0

    def wait_for_sync(self, database_id: int, timeout_seconds: float = 180.0) -> bool:
        """Poll until Metabase has discovered fields for the database."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.database_field_count(database_id) > 0:
                logger.info("metabase_sync_complete", database_id=database_id)
                return True
            time.sleep(3.0)
        logger.warning("metabase_sync_timeout", database_id=database_id)
        return False

    # ── queries ───────────────────────────────────────────────────────────

    def run_native_query(self, database_id: int, sql: str) -> dict:
        """
        Execute a native SQL query through Metabase and normalize the result.

        Returns {"columns": [...], "rows": [[...]], "row_count": int}.
        """
        payload = {
            "type": "native",
            "native": {"query": sql},
            "database": database_id,
        }
        result = self._request("POST", "/api/dataset", json=payload)

        status = (result or {}).get("status")
        if status == "failed":
            message = result.get("error") or result.get("error_type") or "unknown error"
            raise MetabaseError(f"Query failed: {message}")

        data = (result or {}).get("data", {})
        columns = [c.get("display_name") or c.get("name") for c in data.get("cols", [])]
        rows = data.get("rows", [])
        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    # ── cards ─────────────────────────────────────────────────────────────

    def list_cards(self) -> list[CardSummary]:
        result = self._request("GET", "/api/card")
        cards = []
        for c in result or []:
            if c.get("archived"):
                continue
            cards.append(
                CardSummary(
                    id=c["id"],
                    name=c.get("name", ""),
                    description=c.get("description") or "",
                    display=c.get("display", "table"),
                )
            )
        return cards

    def create_native_card(
        self,
        name: str,
        sql: str,
        database_id: int,
        display: str = "table",
        description: str = "",
        visualization_settings: Optional[dict] = None,
        collection_id: Optional[int] = None,
    ) -> dict:
        payload = {
            "name": name,
            "description": description or None,
            "display": display,
            "visualization_settings": visualization_settings or {},
            "dataset_query": {
                "type": "native",
                "native": {"query": sql, "template-tags": {}},
                "database": database_id,
            },
            "collection_id": collection_id,
        }
        card = self._request("POST", "/api/card", json=payload)
        logger.info("metabase_card_created", name=name, id=card.get("id"))
        return card

    # ── dashboards ────────────────────────────────────────────────────────

    def list_dashboards(self) -> list[DashboardSummary]:
        result = self._request("GET", "/api/dashboard")
        dashboards = []
        for d in result or []:
            if d.get("archived"):
                continue
            dashboards.append(
                DashboardSummary(
                    id=d["id"],
                    name=d.get("name", ""),
                    description=d.get("description") or "",
                )
            )
        return dashboards

    def find_dashboard_by_name(self, name: str) -> Optional[DashboardSummary]:
        for d in self.list_dashboards():
            if d.name == name:
                return d
        return None

    def get_dashboard(self, dashboard_id: int) -> dict:
        return self._request("GET", f"/api/dashboard/{dashboard_id}")

    def create_dashboard(self, name: str, description: str = "") -> dict:
        payload = {"name": name, "description": description or None}
        dashboard = self._request("POST", "/api/dashboard", json=payload)
        logger.info("metabase_dashboard_created", name=name, id=dashboard.get("id"))
        return dashboard

    def add_card_to_dashboard(
        self,
        dashboard_id: int,
        card_id: int,
        size_x: int = 12,
        size_y: int = 8,
    ) -> dict:
        """
        Append a card to a dashboard, stacking it below whatever is there.

        Metabase replaces the whole dashcard list on PUT, so existing cards
        are read back and re-sent alongside the new one.
        """
        dashboard = self.get_dashboard(dashboard_id)
        existing = dashboard.get("dashcards", []) or []

        next_row = 0
        for dc in existing:
            bottom = (dc.get("row") or 0) + (dc.get("size_y") or 0)
            next_row = max(next_row, bottom)

        new_card = {
            "id": -1,  # negative id tells Metabase this dashcard is new
            "card_id": card_id,
            "row": next_row,
            "col": 0,
            "size_x": size_x,
            "size_y": size_y,
            "parameter_mappings": [],
            "visualization_settings": {},
        }

        payload = {"dashcards": existing + [new_card]}
        result = self._request("PUT", f"/api/dashboard/{dashboard_id}", json=payload)
        logger.info("metabase_card_added_to_dashboard", dashboard=dashboard_id, card=card_id)
        return result

    def enable_dashboard_embedding(self, dashboard_id: int) -> None:
        self._request(
            "PUT", f"/api/dashboard/{dashboard_id}", json={"enable_embedding": True}
        )

    def enable_card_embedding(self, card_id: int) -> None:
        self._request("PUT", f"/api/card/{card_id}", json={"enable_embedding": True})

    def close(self) -> None:
        self._http.close()
