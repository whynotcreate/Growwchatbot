"""Groq adapter for the Phase 9 ``LLMClient`` interface.

The model id and API key come from ``.env`` via :class:`src.config.Settings`.
No retrieval happens here; the caller passes the fully built prompt.
"""

from __future__ import annotations

from typing import Any

from src.config import Settings, load_settings
from src.generate.answer import GenerationError

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TOKENS = 256
TEMPERATURE = 0.0


class GroqClient:
    """Minimal Groq chat-completions adapter."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        settings = settings or load_settings()
        self.api_key = api_key or settings.groq_api_key
        self.model = model or settings.llm_model

        if not self.api_key:
            raise GenerationError(
                "GROQ_API_KEY is not set. Add it to .env before generating "
                "answers."
            )
        if not self.model:
            raise GenerationError(
                "LLM_MODEL is not set. Add it to .env before generating answers."
            )

        if client is not None:
            self._client = client
            return

        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - depends on install
            raise GenerationError(
                "The groq package is not installed. Run: "
                "pip install -r requirements.txt"
            ) from exc

        self._client = Groq(
            api_key=self.api_key, timeout=DEFAULT_TIMEOUT_SECONDS
        )

    def generate(self, system: str, user: str) -> str:
        """Call Groq and return the model's text."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
        except Exception as exc:
            raise GenerationError(f"Groq request failed: {exc}") from exc

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise GenerationError(
                "Groq returned an unexpected response."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise GenerationError("Groq returned an empty answer.")
        return content.strip()
