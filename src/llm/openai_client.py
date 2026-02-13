"""OpenAI LLM client."""

from __future__ import annotations

import logging

from openai import OpenAI

from src.config import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS
from src.llm.base import BaseLLMClient

log = logging.getLogger(__name__)


class OpenAIClient(BaseLLMClient):
    def __init__(self) -> None:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set in .env")
        self._client = OpenAI(api_key=OPENAI_API_KEY)
        self._model = LLM_MODEL

    def analyze(self, market_state_json: str, system_prompt: str) -> str:
        log.info(f"Calling OpenAI ({self._model})...")
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Here is the current Market State. Analyze and return "
                        "your trade plan as STRICT JSON matching the required schema.\n\n"
                        f"{market_state_json}"
                    ),
                },
            ],
        )
        content = resp.choices[0].message.content or ""
        log.info(
            f"LLM response: {resp.usage.prompt_tokens}p/{resp.usage.total_tokens}t tokens"
        )
        return content
