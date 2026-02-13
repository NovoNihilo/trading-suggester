"""LLM client factory. Add new providers here."""

from __future__ import annotations

from src.config import LLM_PROVIDER
from src.llm.base import BaseLLMClient


def get_llm_client() -> BaseLLMClient:
    if LLM_PROVIDER == "openai":
        from src.llm.openai_client import OpenAIClient
        return OpenAIClient()
    # Future: add anthropic, local, etc.
    # elif LLM_PROVIDER == "anthropic":
    #     from src.llm.anthropic_client import AnthropicClient
    #     return AnthropicClient()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")
