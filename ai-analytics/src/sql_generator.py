"""
LLM-powered SQL generation (LangChain).

Takes a user question, schema context, and conversation history,
then generates a ClickHouse-compatible SQL query via the configured
LangChain chat model, using structured output so the query and the
model's reasoning arrive as typed fields instead of parsed markdown.
"""

import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.llm import get_chat_model, LLMConfigError
from src.logger import get_logger

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("prompt_not_found", path=str(path))
    return ""


class SQLGenerationResult(BaseModel):
    """Structured output for SQL generation."""
    reasoning: str = Field(description="1-2 sentence explanation of the query approach")
    sql: str = Field(description="A single read-only ClickHouse SQL query with no markdown fences")


class SQLGenerator:
    """
    Generates ClickHouse SQL from natural-language questions.

    Uses the configured LangChain chat model with schema context and
    conversation history to produce accurate, safe, analytical SQL.
    """

    def __init__(self):
        self._system_prompt = _load_prompt("sql_generation.txt")
        self._structured_llm = None

    def _get_structured_llm(self):
        """Lazily build the structured-output LLM."""
        if self._structured_llm is None:
            model = get_chat_model(temperature=0.0)
            self._structured_llm = model.with_structured_output(SQLGenerationResult)
        return self._structured_llm

    def generate_sql(
        self,
        question: str,
        schema_description: str,
        conversation_context: str = "",
        error_context: str = "",
    ) -> dict:
        """
        Generate a ClickHouse SQL query from a natural-language question.

        Args:
            question: The user's question.
            schema_description: Formatted schema context string.
            conversation_context: Prior conversation for follow-up understanding.
            error_context: Previous SQL error for self-correction.

        Returns:
            dict with keys:
                - sql: The generated SQL query string
                - reasoning: LLM's explanation of the query
        """
        try:
            llm = self._get_structured_llm()
        except LLMConfigError as e:
            raise LLMError(str(e)) from e

        user_prompt_parts = [f"## Database Schema\n\n{schema_description}"]

        if conversation_context:
            user_prompt_parts.append(f"## Conversation History\n\n{conversation_context}")

        if error_context:
            user_prompt_parts.append(f"## Previous Error (please fix)\n\n{error_context}")

        user_prompt_parts.append(f"## User Question\n\n{question}")
        user_prompt_parts.append(
            "## Instructions\n\n"
            "Generate a single ClickHouse SQL query to answer the user's question."
        )

        full_prompt = "\n\n".join(user_prompt_parts)

        logger.info("generating_sql", question=question[:100])

        try:
            result: SQLGenerationResult = llm.invoke([
                SystemMessage(content=self._system_prompt),
                HumanMessage(content=full_prompt),
            ])
        except Exception as e:
            logger.error("sql_generation_failed", error=str(e))
            raise LLMError(f"Failed to generate SQL: {str(e)}") from e

        sql = self._strip_fences(result.sql)

        logger.info("sql_generated", sql=sql[:100] if sql else "NONE")

        return {"sql": sql, "reasoning": result.reasoning.strip()}

    def _strip_fences(self, text: str) -> str:
        """Defensively remove markdown code fences some models still add."""
        match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip()


class LLMError(Exception):
    """Raised when the LLM call fails."""
    pass
