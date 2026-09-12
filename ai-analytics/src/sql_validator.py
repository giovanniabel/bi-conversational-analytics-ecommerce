"""
SQL validation and safety layer.

Uses an allowlist approach to ensure only read-only analytical
queries are executed. Rejects any data-modifying or schema-changing SQL.
"""

import re
from dataclasses import dataclass

from src.logger import get_logger

logger = get_logger(__name__)


# Statements that are explicitly allowed
ALLOWED_STATEMENT_PREFIXES = {"select", "with"}

# Keywords that must NEVER appear as statement-level operations
FORBIDDEN_KEYWORDS = {
    "drop", "truncate", "delete", "alter", "insert", "update",
    "create", "attach", "detach", "optimize", "grant", "revoke",
    "rename", "exchange", "kill", "system", "set",
}


@dataclass
class ValidationResult:
    """Result of SQL validation."""
    is_valid: bool
    error_message: str = ""
    cleaned_sql: str = ""


class SQLValidator:
    """
    Validates SQL queries before execution against ClickHouse.

    Approach:
    - Allowlist: only SELECT / WITH...SELECT queries are permitted.
    - Blocklist: explicitly reject any dangerous keywords.
    - Structural: reject multiple statements, suspicious patterns.
    """

    def __init__(self, known_tables: set[str] | None = None, database: str = "olist"):
        self._known_tables = known_tables or set()
        self._database = database

    def validate(self, sql: str) -> ValidationResult:
        """
        Validate a SQL query for safety and correctness.

        Args:
            sql: The SQL query string to validate.

        Returns:
            ValidationResult indicating if the query is safe to execute.
        """
        if not sql or not sql.strip():
            return ValidationResult(False, "Empty SQL query")

        cleaned = self._clean_sql(sql)

        # Check 1: Multiple statements
        check = self._check_multiple_statements(cleaned)
        if not check.is_valid:
            return check

        # Check 2: Allowed statement type
        check = self._check_statement_type(cleaned)
        if not check.is_valid:
            return check

        # Check 3: Forbidden keywords
        check = self._check_forbidden_keywords(cleaned)
        if not check.is_valid:
            return check

        # Check 4: Table references (if known tables provided)
        check = self._check_table_references(cleaned)
        if not check.is_valid:
            return check

        logger.info("sql_validation_passed", sql=cleaned[:100])
        return ValidationResult(True, cleaned_sql=cleaned)

    def _clean_sql(self, sql: str) -> str:
        """Remove markdown code blocks, extra whitespace, trailing semicolons."""
        # Strip markdown SQL code blocks
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql)
        sql = sql.strip()

        # Remove trailing semicolons
        sql = sql.rstrip(";").strip()

        return sql

    def _check_multiple_statements(self, sql: str) -> ValidationResult:
        """Reject queries containing multiple statements."""
        # Remove semicolons inside string literals before checking
        # Simple heuristic: split on ; outside of quotes
        in_single_quote = False
        in_double_quote = False
        semicolons = 0

        for char in sql:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif char == ";" and not in_single_quote and not in_double_quote:
                semicolons += 1

        if semicolons > 0:
            return ValidationResult(False, "Multiple SQL statements are not allowed")

        return ValidationResult(True)

    def _check_statement_type(self, sql: str) -> ValidationResult:
        """Ensure the query starts with an allowed statement type."""
        # Strip inline/block comments to find the real first keyword
        stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        stripped = re.sub(r"--[^\n]*", " ", stripped)
        stripped = stripped.strip()

        first_word = stripped.split()[0].lower() if stripped.split() else ""

        if first_word not in ALLOWED_STATEMENT_PREFIXES:
            return ValidationResult(
                False,
                f"Only SELECT queries are allowed. Got: '{first_word.upper()}...'"
            )

        return ValidationResult(True)

    def _check_forbidden_keywords(self, sql: str) -> ValidationResult:
        """
        Check for forbidden keywords used as SQL commands.

        We look for forbidden words that appear as standalone tokens
        (word boundaries), ignoring them inside string literals or
        column/table names.
        """
        # Remove string literals to avoid false positives
        sanitized = re.sub(r"'[^']*'", "''", sql)
        sanitized = re.sub(r'"[^"]*"', '""', sanitized)

        for keyword in FORBIDDEN_KEYWORDS:
            # Match as a standalone word (not part of a column name)
            pattern = rf"\b{keyword}\b"
            match = re.search(pattern, sanitized, re.IGNORECASE)
            if match:
                # Allow "set" only when it appears in context like "settings" or as part of
                # function names, but block standalone SET statements
                if keyword == "set":
                    # Check if it's at the start of a clause/statement
                    before = sanitized[:match.start()].strip()
                    if before == "" or before.endswith(";"):
                        return ValidationResult(
                            False,
                            f"Forbidden SQL operation detected: {keyword.upper()}"
                        )
                    continue

                return ValidationResult(
                    False,
                    f"Forbidden SQL operation detected: {keyword.upper()}"
                )

        return ValidationResult(True)

    def _check_table_references(self, sql: str) -> ValidationResult:
        """
        Check that referenced tables exist in the known schema.

        This is a best-effort check — it may miss some edge cases
        with complex subqueries or CTEs.
        """
        if not self._known_tables:
            return ValidationResult(True)

        # CTEs (WITH x AS (...)) define query-local names that are legal
        # FROM/JOIN targets even though they're not real tables.
        cte_names = self._extract_cte_names(sql)

        # Extract table names from FROM and JOIN clauses
        # Pattern: FROM/JOIN [database.]table_name
        table_pattern = rf"(?:FROM|JOIN)\s+(?:{re.escape(self._database)}\.)?(\w+)"
        referenced = re.findall(table_pattern, sql, re.IGNORECASE)

        for table in referenced:
            # Skip subquery aliases and CTEs
            if table.lower() in {"select", "lateral", "unnest"}:
                continue
            if table in cte_names:
                continue
            if table not in self._known_tables:
                return ValidationResult(
                    False,
                    f"Unknown table referenced: '{table}'. "
                    f"Available tables: {', '.join(sorted(self._known_tables))}"
                )

        return ValidationResult(True)

    def _extract_cte_names(self, sql: str) -> set[str]:
        """Extract names defined by WITH ... AS ( ... ) common table expressions."""
        return set(re.findall(r"(?:\bWITH\b|,)\s+(\w+)\s+AS\s*\(", sql, re.IGNORECASE))
