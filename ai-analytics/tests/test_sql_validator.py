"""
Unit tests for the SQL Validator.

Tests the most safety-critical component — ensuring destructive
and schema-modifying queries are always rejected.
"""

import pytest
from src.sql_validator import SQLValidator, ValidationResult


@pytest.fixture
def validator():
    """Create a validator with known test tables."""
    known_tables = {
        "customers", "orders", "order_items", "order_payments",
        "order_reviews", "products", "sellers", "geolocation",
        "product_category_name_translation",
    }
    return SQLValidator(known_tables=known_tables, database="olist")


class TestAllowedQueries:
    """Test that valid read-only queries are accepted."""

    def test_simple_select(self, validator):
        result = validator.validate("SELECT count() FROM olist.orders")
        assert result.is_valid

    def test_select_with_join(self, validator):
        sql = """
        SELECT o.order_id, c.customer_city
        FROM olist.orders o
        JOIN olist.customers c ON o.customer_id = c.customer_id
        LIMIT 10
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_select_with_aggregation(self, validator):
        sql = """
        SELECT
            toYYYYMM(order_purchase_timestamp) as month,
            count() as orders,
            sum(price) as revenue
        FROM olist.orders o
        JOIN olist.order_items oi ON o.order_id = oi.order_id
        GROUP BY month
        ORDER BY month
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_with_cte(self, validator):
        sql = """
        WITH monthly AS (
            SELECT toStartOfMonth(order_purchase_timestamp) as month,
                   count() as cnt
            FROM olist.orders
            GROUP BY month
        )
        SELECT * FROM monthly ORDER BY month
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_subquery(self, validator):
        sql = """
        SELECT customer_state, count() as cnt
        FROM olist.customers
        WHERE customer_id IN (
            SELECT customer_id FROM olist.orders
        )
        GROUP BY customer_state
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_markdown_code_block_cleaning(self, validator):
        sql = "```sql\nSELECT count() FROM olist.orders\n```"
        result = validator.validate(sql)
        assert result.is_valid


class TestForbiddenOperations:
    """Test that destructive/modifying operations are always rejected."""

    def test_drop_table(self, validator):
        result = validator.validate("DROP TABLE olist.orders")
        assert not result.is_valid
        assert "DROP" in result.error_message

    def test_truncate_table(self, validator):
        result = validator.validate("TRUNCATE TABLE olist.orders")
        assert not result.is_valid
        assert "TRUNCATE" in result.error_message

    def test_delete(self, validator):
        result = validator.validate("DELETE FROM olist.orders WHERE 1=1")
        assert not result.is_valid
        assert "DELETE" in result.error_message

    def test_insert(self, validator):
        result = validator.validate("INSERT INTO olist.orders VALUES (1,2,3)")
        assert not result.is_valid

    def test_update(self, validator):
        result = validator.validate("UPDATE olist.orders SET order_status = 'x'")
        assert not result.is_valid

    def test_alter_table(self, validator):
        result = validator.validate("ALTER TABLE olist.orders DROP COLUMN customer_id")
        assert not result.is_valid

    def test_create_table(self, validator):
        result = validator.validate("CREATE TABLE olist.test (id UInt32) ENGINE = MergeTree()")
        assert not result.is_valid

    def test_attach(self, validator):
        result = validator.validate("ATTACH TABLE olist.orders")
        assert not result.is_valid

    def test_detach(self, validator):
        result = validator.validate("DETACH TABLE olist.orders")
        assert not result.is_valid

    def test_optimize(self, validator):
        result = validator.validate("OPTIMIZE TABLE olist.orders")
        assert not result.is_valid

    def test_grant(self, validator):
        result = validator.validate("GRANT ALL ON olist.* TO admin")
        assert not result.is_valid

    def test_revoke(self, validator):
        result = validator.validate("REVOKE ALL ON olist.* FROM admin")
        assert not result.is_valid


class TestMultipleStatements:
    """Test that multiple statements are rejected."""

    def test_two_selects(self, validator):
        result = validator.validate("SELECT 1; SELECT 2")
        assert not result.is_valid
        assert "Multiple" in result.error_message

    def test_select_then_drop(self, validator):
        result = validator.validate("SELECT 1; DROP TABLE olist.orders")
        assert not result.is_valid

    def test_semicolon_in_string(self, validator):
        """Semicolons inside string literals should be OK."""
        sql = "SELECT * FROM olist.customers WHERE customer_city = 'a;b' LIMIT 1"
        result = validator.validate(sql)
        assert result.is_valid


class TestTableValidation:
    """Test that unknown table references are caught."""

    def test_unknown_table(self, validator):
        result = validator.validate("SELECT * FROM olist.nonexistent_table LIMIT 1")
        assert not result.is_valid
        assert "Unknown table" in result.error_message

    def test_known_table(self, validator):
        result = validator.validate("SELECT count() FROM olist.orders")
        assert result.is_valid

    def test_multiple_known_tables(self, validator):
        sql = """
        SELECT o.order_id
        FROM olist.orders o
        JOIN olist.order_items oi ON o.order_id = oi.order_id
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_cte_name_not_treated_as_unknown_table(self, validator):
        sql = """
        WITH monthly AS (
            SELECT toStartOfMonth(order_purchase_timestamp) as month, count() as cnt
            FROM olist.orders
            GROUP BY month
        )
        SELECT * FROM monthly ORDER BY month
        """
        result = validator.validate(sql)
        assert result.is_valid

    def test_multiple_ctes(self, validator):
        sql = """
        WITH a AS (SELECT customer_id FROM olist.customers),
             b AS (SELECT order_id FROM olist.orders)
        SELECT * FROM a JOIN b ON 1=1
        """
        result = validator.validate(sql)
        assert result.is_valid


class TestEdgeCases:
    """Test edge cases and unusual inputs."""

    def test_empty_string(self, validator):
        result = validator.validate("")
        assert not result.is_valid

    def test_whitespace_only(self, validator):
        result = validator.validate("   \n\t  ")
        assert not result.is_valid

    def test_trailing_semicolon_removed(self, validator):
        result = validator.validate("SELECT count() FROM olist.orders;")
        assert result.is_valid

    def test_sql_comments(self, validator):
        sql = "-- this is a comment\nSELECT count() FROM olist.orders"
        result = validator.validate(sql)
        assert result.is_valid

    def test_block_comment(self, validator):
        sql = "/* block comment */ SELECT count() FROM olist.orders"
        result = validator.validate(sql)
        assert result.is_valid

    def test_delete_in_column_name(self, validator):
        """A forbidden word used as part of a longer identifier (e.g. an alias)
        should NOT trigger a block — \\b doesn't match between 'e' and '_'."""
        sql = "SELECT order_status, count() as delete_count FROM olist.orders GROUP BY order_status"
        result = validator.validate(sql)
        assert result.is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
