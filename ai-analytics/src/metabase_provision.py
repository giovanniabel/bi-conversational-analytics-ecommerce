"""
Idempotent Metabase provisioning.

Takes a blank Metabase instance to a usable state with no clicking:
first-run setup, a ClickHouse connection, static embedding enabled, and
a starter dashboard. Safe to run repeatedly — every step checks for its
own result before doing anything.

Run standalone with:  python -m src.metabase_provision
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import clickhouse_config, metabase_config
from src.logger import get_logger, setup_logging
from src.metabase_client import MetabaseClient, MetabaseError

logger = get_logger(__name__)


@dataclass
class ProvisionResult:
    database_id: int
    dashboard_id: int
    embedding_secret: str = ""
    created_setup: bool = False
    created_database: bool = False
    created_dashboard: bool = False


# Starter cards for the auto-built dashboard. Kept deliberately close to the
# Streamlit "Orders at a glance" panel so both UIs tell the same story.
STARTER_CARDS: list[dict] = [
    {
        "name": "Total Orders",
        "display": "scalar",
        "description": "All orders in the dataset.",
        "sql": "SELECT count() AS total_orders FROM olist.orders",
        "size_x": 8,
        "size_y": 4,
    },
    {
        "name": "Total Revenue",
        "display": "scalar",
        "description": "Sum of all customer payments.",
        "sql": "SELECT round(sum(payment_value), 2) AS total_revenue FROM olist.order_payments",
        "size_x": 8,
        "size_y": 4,
    },
    {
        "name": "Average Order Value",
        "display": "scalar",
        "description": "Mean total payment per order.",
        "sql": (
            "SELECT round(avg(order_total), 2) AS avg_order_value FROM ("
            "SELECT order_id, sum(payment_value) AS order_total "
            "FROM olist.order_payments GROUP BY order_id)"
        ),
        "size_x": 8,
        "size_y": 4,
    },
    {
        "name": "Orders per Month",
        "display": "line",
        "description": "Order volume over time.",
        "sql": (
            "SELECT toStartOfMonth(order_purchase_timestamp) AS month, count() AS orders "
            "FROM olist.orders GROUP BY month ORDER BY month"
        ),
        "size_x": 24,
        "size_y": 8,
    },
    {
        "name": "Top Seller Cities by Orders",
        "display": "row",
        "description": "Where the sellers fulfilling orders are based.",
        "sql": (
            "SELECT s.seller_city AS city, count() AS orders "
            "FROM olist.order_items oi "
            "JOIN olist.sellers s ON oi.seller_id = s.seller_id "
            "GROUP BY city ORDER BY orders DESC LIMIT 10"
        ),
        "size_x": 12,
        "size_y": 8,
    },
    {
        "name": "Top Product Categories by Revenue",
        "display": "row",
        "description": "Highest-grossing product categories.",
        "sql": (
            "SELECT coalesce(t.product_category_name_english, p.product_category_name, 'unknown') AS category, "
            "round(sum(oi.price + oi.freight_value), 2) AS revenue "
            "FROM olist.order_items oi "
            "JOIN olist.products p ON oi.product_id = p.product_id "
            "LEFT JOIN olist.product_category_name_translation t "
            "ON p.product_category_name = t.product_category_name "
            "GROUP BY category ORDER BY revenue DESC LIMIT 10"
        ),
        "size_x": 12,
        "size_y": 8,
    },
]


def _ensure_setup(client: MetabaseClient) -> bool:
    """Run the first-run wizard if this Metabase has never been initialized."""
    if client.has_user_setup():
        logger.info("metabase_setup_already_done")
        client.authenticate()
        return False

    logger.info("metabase_running_first_time_setup")
    client.run_setup(
        email=metabase_config.admin_email,
        password=metabase_config.admin_password,
        site_name=metabase_config.site_name,
    )
    return True


def _ensure_database(client: MetabaseClient) -> tuple[int, bool]:
    """Register the ClickHouse connection if Metabase doesn't have it yet."""
    existing = client.find_database_by_name(metabase_config.database_name)
    if existing:
        logger.info("metabase_database_exists", id=existing["id"])
        return existing["id"], False

    db = client.create_clickhouse_database(
        name=metabase_config.database_name,
        host=metabase_config.clickhouse_host,
        port=clickhouse_config.http_port,
        user=clickhouse_config.user,
        password=clickhouse_config.password,
        dbname=clickhouse_config.database,
    )
    database_id = db["id"]
    client.wait_for_sync(database_id)
    return database_id, True


def _ensure_embedding_enabled(client: MetabaseClient) -> str:
    """
    Turn on static (signed) embedding and return the secret used to sign JWTs.

    The toggle was renamed across Metabase versions, so both spellings are
    attempted; a failure on either is non-fatal — the UI falls back to
    linking out to Metabase when embedding is unavailable.

    The secret is whatever Metabase itself is using, unless an operator
    pinned one via METABASE_EMBEDDING_SECRET.
    """
    for key in ("enable-embedding-static", "enable-embedding"):
        try:
            client.set_setting(key, True)
            logger.info("metabase_embedding_enabled", setting=key)
        except MetabaseError as e:
            logger.info("metabase_embedding_setting_skipped", setting=key, reason=str(e)[:120])

    if metabase_config.embedding_secret:
        try:
            client.set_setting("embedding-secret-key", metabase_config.embedding_secret)
            logger.info("metabase_embedding_secret_set_from_config")
            return metabase_config.embedding_secret
        except MetabaseError as e:
            logger.warning("metabase_embedding_secret_write_failed", error=str(e)[:200])

    secret = client.get_setting("embedding-secret-key")
    if isinstance(secret, dict):  # some versions wrap the value
        secret = secret.get("value")
    if secret:
        logger.info("metabase_embedding_secret_read_from_instance")
        return str(secret)

    logger.warning("metabase_embedding_secret_unavailable")
    return ""


def _ensure_dashboard(client: MetabaseClient, database_id: int) -> tuple[int, bool]:
    """Build the starter dashboard once; leave it alone on later runs."""
    existing = client.find_dashboard_by_name(metabase_config.dashboard_name)
    if existing:
        logger.info("metabase_dashboard_exists", id=existing.id)
        try:
            client.enable_dashboard_embedding(existing.id)
        except MetabaseError:
            pass
        return existing.id, False

    dashboard = client.create_dashboard(
        name=metabase_config.dashboard_name,
        description="Auto-provisioned overview of the Olist e-commerce dataset.",
    )
    dashboard_id = dashboard["id"]

    for spec in STARTER_CARDS:
        try:
            card = client.create_native_card(
                name=spec["name"],
                sql=spec["sql"],
                database_id=database_id,
                display=spec["display"],
                description=spec["description"],
            )
            client.add_card_to_dashboard(
                dashboard_id=dashboard_id,
                card_id=card["id"],
                size_x=spec["size_x"],
                size_y=spec["size_y"],
            )
        except MetabaseError as e:
            # One bad card shouldn't cost us the whole dashboard.
            logger.warning("metabase_starter_card_failed", card=spec["name"], error=str(e)[:200])

    try:
        client.enable_dashboard_embedding(dashboard_id)
    except MetabaseError as e:
        logger.warning("metabase_dashboard_embedding_failed", error=str(e)[:200])

    return dashboard_id, True


def provision(client: Optional[MetabaseClient] = None) -> ProvisionResult:
    """Bring Metabase to a ready state. Idempotent."""
    owns_client = client is None
    if client is None:
        client = MetabaseClient(
            internal_url=metabase_config.internal_url,
            username=metabase_config.admin_email,
            password=metabase_config.admin_password,
        )

    try:
        client.wait_until_ready()
        created_setup = _ensure_setup(client)
        database_id, created_database = _ensure_database(client)
        embedding_secret = _ensure_embedding_enabled(client)
        dashboard_id, created_dashboard = _ensure_dashboard(client, database_id)

        logger.info(
            "metabase_provisioned",
            database_id=database_id,
            dashboard_id=dashboard_id,
            embedding=bool(embedding_secret),
            created_setup=created_setup,
            created_database=created_database,
            created_dashboard=created_dashboard,
        )
        return ProvisionResult(
            database_id=database_id,
            dashboard_id=dashboard_id,
            embedding_secret=embedding_secret,
            created_setup=created_setup,
            created_database=created_database,
            created_dashboard=created_dashboard,
        )
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    setup_logging()
    result = provision()
    print(
        f"Metabase ready — database_id={result.database_id} "
        f"dashboard_id={result.dashboard_id}"
    )
