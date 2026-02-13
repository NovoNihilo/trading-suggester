"""Anthropic LLM client."""

from __future__ import annotations

import logging

from anthropic import Anthropic

from src.config import ANTHROPIC_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS
from src.llm.base import BaseLLMClient

log = logging.getLogger(__name__)


class AnthropicClient(BaseLLMClient):
    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        self._client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self._model = LLM_MODEL

    def analyze(self, market_state_json: str, system_prompt: str) -> str:
        log.info(f"Calling Anthropic ({self._model})...")
        resp = self._client.messages.create(
            model=self._model,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Here is the current Market State. Analyze and return "
                        "your trade plan as STRICT JSON matching the required schema. "
                        "Return ONLY the JSON object, no markdown fences, no explanation.\n\n"
                        f"{market_state_json}"
                    ),
                },
            ],
        )
        content = resp.content[0].text
        log.info(
            f"LLM response: {resp.usage.input_tokens}in/{resp.usage.output_tokens}out tokens"
        )
        return content