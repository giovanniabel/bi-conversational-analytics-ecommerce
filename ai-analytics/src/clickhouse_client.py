"""
ClickHouse client wrapper.

Provides read-only query execution with timing, error handling,
and connection health checks.
"""

import time
from dataclasses import dataclass
from typing import Optional

import clickhouse_connect
import pandas as pd

from src.config import clickhouse_config
from src.logger import get_logger

logger = get_logger(__name__)


@dataclass
class QueryResult:
    """Result of a ClickHouse query execution."""
    dataframe: pd.DataFrame
    row_count: int
    column_names: list[str]
    column_types: list[str]
    execution_time_seconds: float
    sql: str


class ClickHouseClient:
    """Read-only ClickHouse client with connection pooling and error handling."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Get or create the ClickHouse connection."""
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=clickhouse_config.host,
                port=clickhouse_config.http_port,
                username=clickhouse_config.user,
                password=clickhouse_config.password,
                database=clickhouse_config.database,
                settings={
                    "readonly": "1",
                    "max_result_rows": "10000",
                },
            )
        return self._client

    def health_check(self) -> tuple[bool, str]:
        """
        Check if ClickHouse is reachable and authenticated.

        Returns:
            (is_healthy, message)
        """
        try:
            client = self._get_client()
            result = client.query("SELECT 1")
            if result.result_rows and result.result_rows[0][0] == 1:
                return True, "Connected to ClickHouse"
            return False, "Unexpected response from ClickHouse"
        except Exception as e:
            self._client = None
            error_msg = str(e)
            if "authentication" in error_msg.lower() or "password" in error_msg.lower():
                return False, "Authentication failed — check CLICKHOUSE_USER and CLICKHOUSE_PASSWORD"
            if "connect" in error_msg.lower() or "refused" in error_msg.lower():
                return False, f"Cannot connect to ClickHouse at {clickhouse_config.host}:{clickhouse_config.http_port}"
            return False, f"ClickHouse error: {error_msg}"

    def execute_query(self, sql: str) -> QueryResult:
        """
        Execute a read-only SQL query and return results as a DataFrame.

        Args:
            sql: The SQL query to execute (must be read-only).

        Returns:
            QueryResult with DataFrame, metadata, and timing.

        Raises:
            ClickHouseQueryError: If the query fails.
        """
        client = self._get_client()

        logger.info("executing_query", sql=sql[:200])

        start_time = time.time()
        try:
            result = client.query(sql)
            execution_time = time.time() - start_time

            columns = result.column_names
            types = [str(t) for t in result.column_types] if result.column_types else []

            df = pd.DataFrame(result.result_rows, columns=columns)

            query_result = QueryResult(
                dataframe=df,
                row_count=len(df),
                column_names=list(columns),
                column_types=types,
                execution_time_seconds=round(execution_time, 3),
                sql=sql,
            )

            logger.info(
                "query_executed",
                row_count=query_result.row_count,
                execution_time=query_result.execution_time_seconds,
            )

            return query_result

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                "query_failed",
                sql=sql[:200],
                error=str(e),
                execution_time=round(execution_time, 3),
            )
            # Reset client on connection errors
            if "connect" in str(e).lower():
                self._client = None
            raise ClickHouseQueryError(str(e), sql) from e

    def close(self):
        """Close the connection."""
        if self._client:
            self._client.close()
            self._client = None


class ClickHouseQueryError(Exception):
    """Raised when a ClickHouse query fails."""

    def __init__(self, message: str, sql: str = ""):
        self.sql = sql
        super().__init__(message)
