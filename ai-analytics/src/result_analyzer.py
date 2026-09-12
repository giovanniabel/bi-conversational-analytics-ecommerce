"""
Result analysis via LangChain.

Takes query results and the original question, then generates
a natural-language analysis grounded in the actual data.
"""

from pathlib import Path
from typing import Literal

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.config import app_config
from src.llm import get_chat_model, LLMConfigError
from src.logger import get_logger

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

ChartType = Literal[
    "line", "bar", "horizontal_bar", "scatter", "pie", "metric", "table", "none"
]


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class ResultAnalysis(BaseModel):
    """Structured output for result interpretation."""
    answer: str = Field(description="Natural-language answer grounded only in the query results")
    chart_type: ChartType = Field(description="Best visualization type for this result shape")


class ResultAnalyzer:
    """
    Analyzes query results using the LLM.

    Sends a compact representation of the data to the LLM
    and asks for a natural-language interpretation grounded
    in the actual results.
    """

    def __init__(self):
        self._system_prompt = _load_prompt("result_analysis.txt")
        self._structured_llm = None

    def _get_structured_llm(self):
        if self._structured_llm is None:
            model = get_chat_model(temperature=0.2)
            self._structured_llm = model.with_structured_output(ResultAnalysis)
        return self._structured_llm

    def analyze(
        self,
        question: str,
        sql: str,
        df: pd.DataFrame,
        conversation_context: str = "",
    ) -> dict:
        """
        Analyze query results and generate a natural-language answer.

        Args:
            question: The original user question.
            sql: The SQL that was executed.
            df: Query result DataFrame.
            conversation_context: Prior conversation for continuity.

        Returns:
            dict with keys:
                - answer: Natural-language analysis
                - chart_suggestion: Suggested chart type (or "none")
        """
        try:
            llm = self._get_structured_llm()
        except LLMConfigError as e:
            logger.error("analysis_llm_unavailable", error=str(e))
            return {
                "answer": f"Query returned {len(df)} rows, but the LLM is not configured: {e}",
                "chart_suggestion": "table",
            }

        data_summary = self._prepare_data_for_llm(df)

        prompt_parts = []
        if conversation_context:
            prompt_parts.append(f"## Conversation History\n\n{conversation_context}")

        prompt_parts.append(f"## User Question\n\n{question}")
        prompt_parts.append(f"## SQL Executed\n\n```sql\n{sql}\n```")
        prompt_parts.append(f"## Query Results ({len(df)} rows)\n\n{data_summary}")
        prompt_parts.append(
            "## Instructions\n\n"
            "Answer the user's question using only the query results above, "
            "and choose the best chart type for this data."
        )

        full_prompt = "\n\n".join(prompt_parts)

        logger.info("analyzing_results", question=question[:100], rows=len(df))

        try:
            result: ResultAnalysis = llm.invoke([
                SystemMessage(content=self._system_prompt),
                HumanMessage(content=full_prompt),
            ])
            logger.info("analysis_complete", chart_type=result.chart_type)
            return {"answer": result.answer.strip(), "chart_suggestion": result.chart_type}

        except Exception as e:
            logger.error("analysis_failed", error=str(e))
            return {
                "answer": f"I retrieved {len(df)} rows but encountered an error analyzing the results: {str(e)}",
                "chart_suggestion": "table",
            }

    def _prepare_data_for_llm(self, df: pd.DataFrame) -> str:
        """
        Prepare a compact text representation of the DataFrame for the LLM.

        For large results, sends first/last rows plus summary statistics
        rather than the full result set, since ClickHouse has already
        done the heavy aggregation.
        """
        max_rows = app_config.max_result_rows_for_llm

        if len(df) == 0:
            return "No results returned."

        if len(df) == 1 and len(df.columns) == 1:
            return f"Result: {df.iloc[0, 0]}"

        if len(df) <= max_rows:
            return df.to_string(index=False, max_rows=max_rows)

        parts = []
        parts.append(f"Total rows: {len(df)} (showing first and last {min(20, len(df))} rows)")
        parts.append("")
        parts.append("First rows:")
        parts.append(df.head(20).to_string(index=False))
        parts.append("")
        parts.append("Last rows:")
        parts.append(df.tail(20).to_string(index=False))
        parts.append("")

        numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if numeric_cols:
            parts.append("Summary statistics:")
            parts.append(df[numeric_cols].describe().to_string())

        return "\n".join(parts)
