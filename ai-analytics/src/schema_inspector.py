"""
Dynamic schema inspector for ClickHouse.

Discovers databases, tables, columns, types, row counts,
and infers relationships from matching column names.
"""

from dataclasses import dataclass, field
from typing import Optional

from src.clickhouse_client import ClickHouseClient
from src.config import clickhouse_config
from src.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ColumnInfo:
    """Metadata for a single column."""
    name: str
    data_type: str
    is_nullable: bool = False


@dataclass
class TableInfo:
    """Metadata for a single table."""
    database: str
    name: str
    engine: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int = 0
    order_by: str = ""


@dataclass
class Relationship:
    """Inferred relationship between two tables via a shared column."""
    from_table: str
    to_table: str
    join_column: str


@dataclass
class SchemaInfo:
    """Complete schema metadata for the target database."""
    database: str
    tables: dict[str, TableInfo] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)


class SchemaInspector:
    """
    Inspects ClickHouse schema dynamically.

    Caches the schema after the first retrieval to avoid
    repeated metadata queries.
    """

    def __init__(self, ch_client: ClickHouseClient):
        self._client = ch_client
        self._schema: Optional[SchemaInfo] = None

    def get_schema(self, force_refresh: bool = False) -> SchemaInfo:
        """
        Retrieve the full schema for the configured database.

        Args:
            force_refresh: If True, re-query even if cached.

        Returns:
            SchemaInfo with tables, columns, and relationships.
        """
        # A cached schema with tables but zero total rows almost always means
        # we caught the database mid-bootstrap (e.g. the docker-compose init
        # script is still loading CSVs into freshly-created tables). Treat
        # that as not-yet-cached so the next call picks up the real data
        # instead of showing "0 rows" for the lifetime of the process.
        cached_but_empty = (
            self._schema is not None
            and self._schema.tables
            and sum(t.row_count for t in self._schema.tables.values()) == 0
        )
        if self._schema is not None and not force_refresh and not cached_but_empty:
            return self._schema

        database = clickhouse_config.database
        logger.info("inspecting_schema", database=database)

        schema = SchemaInfo(database=database)

        # 1. Discover tables
        tables_result = self._client.execute_query(f"""
            SELECT name, engine
            FROM system.tables
            WHERE database = '{database}'
            ORDER BY name
        """)

        for _, row in tables_result.dataframe.iterrows():
            table_name = row["name"]
            schema.tables[table_name] = TableInfo(
                database=database,
                name=table_name,
                engine=row["engine"],
            )

        # 2. Discover columns
        columns_result = self._client.execute_query(f"""
            SELECT
                table,
                name,
                type,
                position
            FROM system.columns
            WHERE database = '{database}'
            ORDER BY table, position
        """)

        for _, row in columns_result.dataframe.iterrows():
            table_name = row["table"]
            if table_name in schema.tables:
                col = ColumnInfo(
                    name=row["name"],
                    data_type=row["type"],
                    is_nullable=row["type"].startswith("Nullable"),
                )
                schema.tables[table_name].columns.append(col)

        # 3. Get row counts
        for table_name in schema.tables:
            try:
                count_result = self._client.execute_query(
                    f"SELECT count() as cnt FROM {database}.{table_name}"
                )
                schema.tables[table_name].row_count = int(
                    count_result.dataframe.iloc[0]["cnt"]
                )
            except Exception as e:
                logger.warning("row_count_failed", table=table_name, error=str(e))

        # 4. Infer relationships via shared column names
        schema.relationships = self._infer_relationships(schema)

        self._schema = schema

        logger.info(
            "schema_inspected",
            tables=len(schema.tables),
            total_columns=sum(len(t.columns) for t in schema.tables.values()),
            relationships=len(schema.relationships),
        )

        return schema

    def _infer_relationships(self, schema: SchemaInfo) -> list[Relationship]:
        """
        Infer table relationships from matching column names.

        Heuristic: If column X exists in table A and table B,
        and X looks like a foreign key (ends with _id, or is a known
        join key like zip_code_prefix), it's likely a join relationship.
        """
        join_key_patterns = {"_id", "zip_code_prefix", "product_category_name"}

        # Build column → table mapping
        column_tables: dict[str, list[str]] = {}
        for table_name, table_info in schema.tables.items():
            for col in table_info.columns:
                is_join_candidate = any(
                    col.name.endswith(p) or col.name == p
                    for p in join_key_patterns
                )
                if is_join_candidate:
                    column_tables.setdefault(col.name, []).append(table_name)

        relationships = []
        for col_name, tables in column_tables.items():
            if len(tables) >= 2:
                # Create pairwise relationships
                for i in range(len(tables)):
                    for j in range(i + 1, len(tables)):
                        relationships.append(Relationship(
                            from_table=tables[i],
                            to_table=tables[j],
                            join_column=col_name,
                        ))

        return relationships

    def get_schema_description(self) -> str:
        """
        Generate a compact text description of the schema for LLM context.

        Returns:
            A formatted string describing all tables, columns, types,
            row counts, and relationships.
        """
        schema = self.get_schema()
        parts = []
        parts.append(f"Database: {schema.database}")
        parts.append("")

        for table_name, table in sorted(schema.tables.items()):
            parts.append(f"Table: {schema.database}.{table_name} ({table.row_count:,} rows)")
            for col in table.columns:
                nullable_flag = " [nullable]" if col.is_nullable else ""
                parts.append(f"  - {col.name}: {col.data_type}{nullable_flag}")
            parts.append("")

        if schema.relationships:
            parts.append("Table Relationships (join keys):")
            for rel in schema.relationships:
                parts.append(f"  - {rel.from_table} <-> {rel.to_table} ON {rel.join_column}")
            parts.append("")

        return "\n".join(parts)

    def get_sample_values(self, table: str, column: str, limit: int = 10) -> list:
        """Get sample distinct values for a column (useful for filter hints)."""
        try:
            result = self._client.execute_query(
                f"SELECT DISTINCT {column} FROM {clickhouse_config.database}.{table} "
                f"LIMIT {limit}"
            )
            return result.dataframe[column].tolist()
        except Exception:
            return []

    def generate_suggested_questions(self) -> list[str]:
        """
        Generate example questions based on the discovered schema.

        Returns context-aware suggested questions the user can ask.
        """
        schema = self.get_schema()
        suggestions = []

        table_names = set(schema.tables.keys())

        # Revenue / sales questions (if order_items or order_payments exist)
        if "order_items" in table_names:
            suggestions.append("What is the total revenue by month?")
            suggestions.append("Which product category generates the most revenue?")
            suggestions.append("What are the top 10 products by total sales?")

        if "orders" in table_names:
            suggestions.append("How many orders were placed each month?")
            suggestions.append("What is the order cancellation rate?")

        if "customers" in table_names:
            suggestions.append("Which state has the most customers?")
            suggestions.append("How many unique customers placed orders?")

        if "order_reviews" in table_names:
            suggestions.append("What is the average review score by product category?")
            suggestions.append("Show the distribution of review scores.")

        if "sellers" in table_names:
            suggestions.append("Which cities have the most sellers?")

        if "order_payments" in table_names:
            suggestions.append("What is the most popular payment method?")
            suggestions.append("What is the average order value by payment type?")

        if "order_items" in table_names and "orders" in table_names:
            suggestions.append("Compare revenue between the first and second half of 2018.")
            suggestions.append("Show the monthly trend of average order value.")

        return suggestions
