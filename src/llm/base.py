"""Base interface for LLM clients. Implement this to add a new provider."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    @abstractmethod
    def analyze(self, market_state_json: str, system_prompt: str) -> str:
        """Send market state to LLM, return raw JSON string response."""
        ...
