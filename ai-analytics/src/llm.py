"""
LangChain chat model factory.

Centralizes provider selection so the rest of the app never imports
a provider-specific LangChain package directly. Swapping providers
is a matter of changing LLM_PROVIDER / LLM_MODEL in the environment.
"""

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from src.config import llm_config
from src.logger import get_logger

logger = get_logger(__name__)

# Maps our LLM_PROVIDER value to LangChain's model_provider identifier
# used by init_chat_model.
_PROVIDER_ALIASES = {
    "gemini": "google_genai",
    "google": "google_genai",
    "google_genai": "google_genai",
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
}


class LLMConfigError(Exception):
    """Raised when the LLM provider is misconfigured."""


@lru_cache(maxsize=None)
def get_chat_model(temperature: float = 0.0) -> BaseChatModel:
    """
    Build (and cache) a LangChain chat model for the configured provider.

    Cached per temperature value so callers that need different
    determinism (e.g. SQL generation vs. narrative analysis) don't
    re-instantiate a client on every call.
    """
    from langchain.chat_models import init_chat_model

    if not llm_config.api_key:
        raise LLMConfigError(
            "LLM_API_KEY is not set. Add it to your .env file to enable AI features."
        )

    provider = _PROVIDER_ALIASES.get(llm_config.provider.lower())
    if provider is None:
        raise LLMConfigError(
            f"Unknown LLM_PROVIDER '{llm_config.provider}'. "
            f"Supported: {sorted(set(_PROVIDER_ALIASES.values()))}"
        )

    logger.info("initializing_llm", provider=provider, model=llm_config.model)

    try:
        return init_chat_model(
            model=llm_config.model,
            model_provider=provider,
            api_key=llm_config.api_key,
            temperature=temperature,
        )
    except Exception as e:
        raise LLMConfigError(f"Failed to initialize LLM ({provider}): {e}") from e
