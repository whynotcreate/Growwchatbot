"""Phase 9 — prompt construction for grounded answers.

The generator receives only the retrieved chunks from Phase 8. The system
prompt forbids outside knowledge, advice, performance math, PII echo, URLs, and
the last-updated line; the application adds the single citation and date after
generation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from src.retrieve.search import Hit

DEFAULT_SYSTEM_ID = "v1"

SYSTEM_PROMPTS: dict[str, str] = {
    "v1": (
        "You are a facts-only assistant for HDFC Mutual Fund scheme pages.\n"
        "Use only the retrieved chunks provided by the user. Do not use outside "
        "knowledge.\n"
        "Answer the exact fact asked in at most 3 short sentences.\n"
        "If more than one chunk contains the answer, use the highest-ranked "
        "chunk (the first one listed).\n"
        "Do not include URLs, markdown links, citations, or a last-updated line; "
        "the application adds the single citation and date.\n"
        "Never give investment advice, recommendations, buy/sell/hold guidance, "
        "suitability opinions, or portfolio allocation.\n"
        "Never calculate or compare returns, CAGR, or performance.\n"
        "Never repeat personal information such as PAN, Aadhaar, phone numbers, "
        "emails, OTPs, or account numbers.\n"
        "If the chunks do not contain the answer, say you could not find it in "
        "the provided sources.\n"
        "Be concise, neutral, and factual."
    ),
}


def build_system_prompt(system_id: str | None = None) -> str:
    """Return the system prompt for ``system_id`` (defaults to v1)."""
    key = (system_id or DEFAULT_SYSTEM_ID).strip()
    try:
        return SYSTEM_PROMPTS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown prompt system_id: {key!r}") from exc


def build_user_prompt(question: str, hits: Sequence["Hit"]) -> str:
    """Build the user/context payload: question plus retrieved chunks."""
    if not question or not question.strip():
        raise ValueError("build_user_prompt() requires a non-empty question")
    if not hits:
        raise ValueError("build_user_prompt() requires at least one retrieved hit")

    lines = [
        f"Question: {question.strip()}",
        "",
        "Retrieved source chunks (use only these):",
    ]
    for index, hit in enumerate(hits, start=1):
        text = " ".join(hit.text.split())
        lines.extend(
            [
                f"[{index}] chunk_id: {hit.chunk_id}",
                f"    section: {hit.section}",
                f"    title: {hit.title}",
                f"    source_url: {hit.source_url}",
                f"    fetched_at: {hit.fetched_at}",
                f"    text: {text}",
                "",
            ]
        )

    lines.append(
        "Answer the question using only the chunks above. "
        "Do not include a URL or a last-updated line."
    )
    return "\n".join(lines).strip() + "\n"
