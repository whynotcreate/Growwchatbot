"""Phase 9 — turn Phase 8 hits into the answer object.

The LLM only writes the factual sentence(s). Python always appends the single
``citation_url`` and ``last_updated_from_sources`` so the model cannot invent a
second link or a stale date. Refused questions and empty retrieval never reach
the provider.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from src.config import Settings, load_settings
from src.generate.prompt import build_system_prompt, build_user_prompt
from src.retrieve.search import RetrievalResult
from src.safety.pii import redact_pii

NOT_FOUND_TEXT = (
    "I couldn't find that in the provided sources. "
    "Please check the official HDFC Mutual Fund page linked here."
)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SOURCE_LINE_RE = re.compile(r"(?im)^\s*(?:source|citation)\s*:.*$")
_SOURCE_INLINE_RE = re.compile(r"(?i)\b(?:source|citation)\s*:\s*")
_LAST_UPDATED_LINE_RE = re.compile(r"(?im)^\s*last\s+updated[^\n]*$")
_LAST_UPDATED_INLINE_RE = re.compile(
    r"(?i)\blast\s+updated(?:\s+from\s+sources)?\s*:\s*\S+"
)
_ANSWER_PREFIX_RE = re.compile(r"(?i)^\s*(?:answer|response)\s*:\s*")
_LINK_LEFTOVER_RE = re.compile(
    r"(?i)^(?:see|read|visit|check|source|citation)\s*[.:;!?]*$"
)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")
_REPEATED_PUNCT_RE = re.compile(r"([.!?])\1+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“(])")


class GenerationError(RuntimeError):
    """Raised when the provider is unavailable or returns unusable output."""


class LLMClient(Protocol):
    """Provider-neutral generator interface (Groq today, others later)."""

    def generate(self, system: str, user: str) -> str:
        """Return the factual answer text for one prompt."""


def not_found_answer(retrieval: RetrievalResult) -> dict[str, Any]:
    """Deterministic answer for an empty / low-score retrieval (no LLM)."""
    return {
        "text": NOT_FOUND_TEXT,
        "citation_url": retrieval.citation_url,
        "last_updated_from_sources": None,
        "refused": False,
        "refusal_reason": None,
    }


def _remove_url(match: re.Match[str]) -> str:
    """Drop a URL but keep punctuation that followed it in the sentence."""
    url = match.group(0)
    trailing = url[len(url.rstrip(".,;:!?)]}")) :]
    return trailing


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence or not any(char.isalnum() for char in sentence):
            continue
        if _LINK_LEFTOVER_RE.match(sentence):
            continue
        sentences.append(sentence)
    return sentences


def _limit_sentences(text: str, limit: int = 3) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    result = " ".join(sentences[:limit])
    if result and result[-1] not in ".!?":
        result += "."
    return result


def sanitize_text(raw: str) -> str:
    """Keep only the factual prose the model should contribute.

    Removes links, citation/source lines, and a model-written last-updated line,
    redacts PII as defense-in-depth, and enforces the three-sentence limit.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    # Markdown links first: keep the label, then remove bare URLs.
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(_remove_url, text)
    text = _SOURCE_LINE_RE.sub("", text)
    text = _SOURCE_INLINE_RE.sub("", text)
    text = _LAST_UPDATED_LINE_RE.sub("", text)
    text = _LAST_UPDATED_INLINE_RE.sub("", text)
    text = _ANSWER_PREFIX_RE.sub("", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _REPEATED_PUNCT_RE.sub(r"\1", text)
    text = " ".join(text.split())
    text = redact_pii(text)
    return _limit_sentences(text, 3)


def build_llm_client(settings: Settings) -> LLMClient:
    """Select the configured provider adapter."""
    provider = (settings.llm_provider or "").strip().lower()
    if provider == "groq":
        from src.generate.providers.groq import GroqClient

        return GroqClient(settings=settings)
    raise GenerationError(f"Unsupported LLM provider: {provider!r}")


def generate_answer(
    question: str,
    retrieval: RetrievalResult,
    *,
    client: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Generate the factual text and attach the retrieval citation and date."""
    if retrieval.status != "ok" or not retrieval.hits:
        return not_found_answer(retrieval)

    if not retrieval.citation_url:
        raise GenerationError("Retrieval did not provide a citation URL")

    settings = settings or load_settings()
    client = client or build_llm_client(settings)

    system = build_system_prompt(settings.prompt_system_id)
    user = build_user_prompt(question, retrieval.hits)
    raw = client.generate(system, user)
    text = sanitize_text(raw)
    if not text:
        raise GenerationError("LLM returned an empty answer")

    return {
        "text": text,
        "citation_url": retrieval.citation_url,
        "last_updated_from_sources": retrieval.last_updated_from_sources,
        "refused": False,
        "refusal_reason": None,
    }
