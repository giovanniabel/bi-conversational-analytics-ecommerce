"""
Centralized configuration loaded from environment variables.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ClickHouseConfig:
    """ClickHouse connection settings."""
    host: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_HOST", "localhost"))
    http_port: int = field(default_factory=lambda: int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")))
    native_port: int = field(default_factory=lambda: int(os.getenv("CLICKHOUSE_NATIVE_PORT", "9000")))
    user: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_USER", "admin"))
    password: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_PASSWORD", "admin123"))
    database: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_DATABASE", "olist"))


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider settings."""
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "gemini"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini-2.5-flash"))


@dataclass(frozen=True)
class AppConfig:
    """Application-level settings."""
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    max_conversation_history: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONVERSATION_HISTORY", "10"))
    )
    max_sql_retry_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_SQL_RETRY_ATTEMPTS", "3"))
    )
    max_result_rows_for_llm: int = field(
        default_factory=lambda: int(os.getenv("MAX_RESULT_ROWS_FOR_LLM", "200"))
    )


# Singleton instances
clickhouse_config = ClickHouseConfig()
llm_config = LLMConfig()
app_config = AppConfig()
