"""Application entry point — safety → retrieval → generation.

This is the only module the UI should call. It never scrapes, chunks, embeds, or
indexes; those stages read their existing artifacts. Refused questions return
the Phase 7 answer directly, empty retrieval returns a deterministic not-found
answer, and only an allowed, grounded question reaches the LLM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.config import Settings, load_settings
from src.generate.answer import generate_answer, not_found_answer
from src.retrieve.search import search
from src.safety.intent import check_question


def answer_question(
    raw_question: str,
    *,
    settings: Settings | None = None,
    embedder: Any | None = None,
    collection: Any | None = None,
    llm_client: Any | None = None,
    retrieval_dir: Path | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Run the Phase 7 → 8 → 9 path and return the answer object."""
    settings = settings or load_settings()

    gate = check_question(raw_question, settings.sources_path)
    if gate.action == "refuse":
        if gate.answer is None:
            raise RuntimeError("Guardrail refusal did not include an answer")
        return gate.answer

    if not gate.question:
        raise RuntimeError("Guardrail allowed an empty question")

    retrieval = search(
        gate.question,
        settings=settings,
        embedder=embedder,
        collection=collection,
        top_k=top_k,
        min_score=min_score,
        intent=gate.intent,
        retrieval_dir=retrieval_dir,
    )

    if retrieval.status != "ok" or not retrieval.hits:
        return not_found_answer(retrieval)

    return generate_answer(
        gate.question,
        retrieval,
        client=llm_client,
        settings=settings,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Answer one question through safety, retrieval, and generation."
    )
    parser.add_argument("--q", required=True, help="Raw user question.")
    parser.add_argument(
        "--top-k", type=int, default=None, help="Override retrieval.top_k."
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Override retrieval.min_score (0–1 cosine similarity).",
    )
    args = parser.parse_args(argv)

    try:
        answer = answer_question(
            args.q, top_k=args.top_k, min_score=args.min_score
        )
    except Exception as exc:
        print(f"Query failed: {type(exc).__name__}: {exc}")
        return 1

    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
