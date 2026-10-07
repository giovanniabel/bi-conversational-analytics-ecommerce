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
class MetabaseConfig:
    """
    Metabase connection settings.

    `internal_url` is how the backend reaches Metabase (Docker service name);
    `public_url` is how the *browser* reaches it, which is what iframe `src`
    attributes must use. They differ inside Docker and must both be right.
    """
    internal_url: str = field(
        default_factory=lambda: os.getenv("METABASE_INTERNAL_URL", "http://localhost:3000")
    )
    public_url: str = field(
        default_factory=lambda: os.getenv("METABASE_PUBLIC_URL", "http://localhost:3000")
    )
    admin_email: str = field(
        default_factory=lambda: os.getenv("METABASE_ADMIN_EMAIL", "admin@example.com")
    )
    admin_password: str = field(
        default_factory=lambda: os.getenv("METABASE_ADMIN_PASSWORD", "metabase123!")
    )
    site_name: str = field(
        default_factory=lambda: os.getenv("METABASE_SITE_NAME", "E-commerce Analytics")
    )
    embedding_secret: str = field(
        default_factory=lambda: os.getenv("METABASE_EMBEDDING_SECRET", "")
    )
    database_name: str = field(
        default_factory=lambda: os.getenv("METABASE_DATABASE_NAME", "Olist ClickHouse")
    )
    # The ClickHouse hostname from *Metabase's* vantage point, which is not
    # necessarily this process's. Metabase runs on the Compose network where
    # ClickHouse answers to `clickhouse`, even when provisioning is driven
    # from the host (where CLICKHOUSE_HOST is localhost).
    clickhouse_host: str = field(
        default_factory=lambda: os.getenv("METABASE_CLICKHOUSE_HOST", "clickhouse")
    )
    dashboard_name: str = field(
        default_factory=lambda: os.getenv("METABASE_DASHBOARD_NAME", "Sales Overview")
    )


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
metabase_config = MetabaseConfig()
app_config = AppConfig()
