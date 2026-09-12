"""
Conversation memory management.

Maintains conversational context with a sliding window strategy
to enable follow-up questions without exceeding token limits.
"""

from dataclasses import dataclass, field
from datetime import datetime

from src.config import app_config
from src.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""
    role: str  # "user" or "assistant"
    content: str
    sql: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ConversationManager:
    """
    Manages conversation history with a sliding window.

    Keeps the last N turns to provide context for follow-up questions
    while avoiding excessive token usage.
    """

    def __init__(self, max_turns: int | None = None):
        self.max_turns = max_turns or app_config.max_conversation_history
        self.history: list[ConversationTurn] = []

    def add_user_message(self, message: str) -> None:
        """Add a user message to the history."""
        self.history.append(ConversationTurn(role="user", content=message))
        self._trim()

    def add_assistant_message(self, message: str, sql: str = "") -> None:
        """Add an assistant response with optional SQL."""
        self.history.append(ConversationTurn(
            role="assistant",
            content=message,
            sql=sql,
        ))
        self._trim()

    def get_context_string(self) -> str:
        """
        Build a compact context string for LLM prompts.

        Includes recent turns with condensed SQL references
        so the LLM can understand follow-up references like
        "that", "the previous month", etc.
        """
        if not self.history:
            return ""

        parts = []
        for turn in self.history:
            if turn.role == "user":
                parts.append(f"User: {turn.content}")
            else:
                # Include SQL reference for assistant turns
                content = turn.content
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"Assistant: {content}")
                if turn.sql:
                    # Include abbreviated SQL for context
                    sql_preview = turn.sql[:300] + "..." if len(turn.sql) > 300 else turn.sql
                    parts.append(f"[SQL used: {sql_preview}]")

        return "\n".join(parts)

    def get_last_sql(self) -> str:
        """Get the SQL from the most recent assistant response."""
        for turn in reversed(self.history):
            if turn.role == "assistant" and turn.sql:
                return turn.sql
        return ""

    def get_last_question(self) -> str:
        """Get the most recent user question."""
        for turn in reversed(self.history):
            if turn.role == "user":
                return turn.content
        return ""

    def clear(self) -> None:
        """Clear all conversation history."""
        self.history.clear()
        logger.info("conversation_cleared")

    def _trim(self) -> None:
        """Keep only the last max_turns * 2 entries (user + assistant pairs)."""
        max_entries = self.max_turns * 2
        if len(self.history) > max_entries:
            self.history = self.history[-max_entries:]

    def __len__(self) -> int:
        return len(self.history)
