"""Phase 8 — retrieve the top-k chunks for an allowed question.

Reads the Chroma collection built in Phase 6 and embeds the question with the
**same** :func:`src.ingest.embedder.get_embedder` used for ingestion. It never
calls Groq and never re-indexes.

Each call writes one inspectable trace to
``data/retrieval/{query_id}.json``:

- ``status`` is ``ok`` when at least one hit survives ``min_score``, otherwise
  ``not_found`` (with one official AMC fallback URL).
- ``citation_url`` is the ``source_url`` of the best surviving hit — exactly one
  URL, never a merged list.
- ``last_updated_from_sources`` is the max ``fetched_at`` among kept hits.

The question is stored PII-redacted, so even a direct call that bypasses Phase 7
cannot put personal data on disk.

Usage::

    python -m src.retrieve.search \\
        --q "What is the exit load of HDFC Large Cap Fund Direct Growth?"
"""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Settings, load_settings
from src.ingest import chroma_client
from src.ingest.embedder import get_embedder
from src.safety.pii import redact_pii

FALLBACK_AMC_URL = "https://groww.in/mutual-funds/amc/hdfc-mutual-funds"


@dataclass(frozen=True)
class Hit:
    """One chunk returned by Chroma."""

    chunk_id: str
    score: float
    text: str
    source_url: str
    title: str
    section: str
    scheme_or_page_id: str
    fetched_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": self.score,
            "source_url": self.source_url,
            "section": self.section,
            "text": self.text,
            "title": self.title,
            "scheme_or_page_id": self.scheme_or_page_id,
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Outcome of one retrieval call (also the on-disk trace)."""

    query_id: str
    asked_at: str
    question: str
    question_redacted: bool
    intent: str
    status: str
    top_k: int
    min_score: float
    hits: tuple[Hit, ...]
    citation_url: str | None
    last_updated_from_sources: str | None
    trace_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "asked_at": self.asked_at,
            "question": self.question,
            "question_redacted": self.question_redacted,
            "intent": self.intent,
            "status": self.status,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "hits": [hit.as_dict() for hit in self.hits],
            "citation_url": self.citation_url,
            "last_updated_from_sources": self.last_updated_from_sources,
        }


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_updated(hits: tuple[Hit, ...]) -> str | None:
    timestamps = [
        parsed
        for parsed in (_parse_timestamp(hit.fetched_at) for hit in hits)
        if parsed is not None
    ]
    if not timestamps:
        return None
    return max(timestamps).date().isoformat()


def _amc_url(sources_path: Path) -> str:
    """One official fallback URL when retrieval finds nothing."""
    try:
        with sources_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("page_type", "")).strip().lower() != "amc":
                    continue
                url = str(row.get("url", "")).strip()
                if url:
                    return url
    except OSError:
        pass
    return FALLBACK_AMC_URL


def _build_hits(result: dict[str, Any], min_score: float) -> tuple[Hit, ...]:
    """Turn a Chroma query result into scored hits, dropping low scores."""
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[Hit] = []
    for chunk_id, text, metadata, distance in zip(
        ids, documents, metadatas, distances
    ):
        if distance is None:
            continue
        # Collection uses cosine distance; Phase 5 vectors are unit length.
        score = max(-1.0, min(1.0, 1.0 - float(distance)))
        if score < min_score:
            continue

        metadata = metadata or {}
        source_url = str(metadata.get("source_url", "")).strip()
        if not source_url:  # a hit we cannot cite is not usable
            continue

        hits.append(
            Hit(
                chunk_id=str(chunk_id),
                score=score,
                text=str(text or ""),
                source_url=source_url,
                title=str(metadata.get("title", "")),
                section=str(metadata.get("section", "")),
                scheme_or_page_id=str(metadata.get("scheme_or_page_id", "")),
                fetched_at=str(metadata.get("fetched_at", "")),
            )
        )

    # Chroma already returns nearest-first; sorting keeps "best hit" explicit
    # for injected collections and makes hits[0] the citation source.
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return tuple(hits)


def search(
    question: str,
    *,
    settings: Settings | None = None,
    embedder: Any | None = None,
    collection: Any | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
    intent: str = "factual",
    retrieval_dir: Path | None = None,
    write_trace: bool = True,
) -> RetrievalResult:
    """Embed ``question``, query Chroma, and persist one retrieval trace."""
    if not question or not question.strip():
        raise ValueError("search() requires a non-empty allowed question")

    settings = settings or load_settings()
    resolved_top_k = settings.retrieval_top_k if top_k is None else top_k
    resolved_min_score = (
        settings.retrieval_min_score if min_score is None else min_score
    )
    if resolved_top_k <= 0:
        raise ValueError("top_k must be positive")

    query_id = uuid.uuid4().hex
    asked_at = _utc_now()
    stored_question = redact_pii(question)
    question_redacted = stored_question != question

    embedder = embedder or get_embedder()
    query_vector = embedder.encode([question])[0]

    if collection is None:
        client = chroma_client.get_client(settings.chroma_persist_dir)
        collection = chroma_client.open_collection(
            client, settings.chroma_collection, settings.embedding_model
        )

    raw = collection.query(
        query_embeddings=[query_vector],
        n_results=resolved_top_k,
        include=["documents", "metadatas", "distances"],
    )
    hits = _build_hits(raw, resolved_min_score)

    if hits:
        status = "ok"
        citation_url = hits[0].source_url
        last_updated = _last_updated(hits)
    else:
        status = "not_found"
        citation_url = _amc_url(settings.sources_path)
        last_updated = None

    trace_path: Path | None = None
    if write_trace:
        directory = retrieval_dir or (
            settings.project_root / "data" / "retrieval"
        )
        directory.mkdir(parents=True, exist_ok=True)
        trace_path = directory / f"{query_id}.json"

    result = RetrievalResult(
        query_id=query_id,
        asked_at=asked_at,
        question=stored_question,
        question_redacted=question_redacted,
        intent=intent,
        status=status,
        top_k=resolved_top_k,
        min_score=resolved_min_score,
        hits=hits,
        citation_url=citation_url,
        last_updated_from_sources=last_updated,
        trace_path=trace_path,
    )

    if trace_path is not None:
        trace_path.write_text(
            json.dumps(result.as_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve top-k chunks for an allowed question."
    )
    parser.add_argument("--q", required=True, help="Allowed user question.")
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
        result = search(
            args.q, top_k=args.top_k, min_score=args.min_score
        )
    except Exception as exc:
        print(f"Retrieval failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"Question: {result.question}")
    print(
        f"Status: {result.status}  (intent: {result.intent}, "
        f"top_k: {result.top_k}, min_score: {result.min_score})"
    )
    for index, hit in enumerate(result.hits, start=1):
        preview = " ".join(hit.text.split())[:100]
        print(f"  {index}. {hit.score:.4f}  [{hit.section}]  {hit.source_url}")
        print(f"     {hit.chunk_id}  {preview}")
    print(f"Citation: {result.citation_url}")
    print(f"Last updated from sources: {result.last_updated_from_sources}")
    if result.trace_path is not None:
        print(f"Trace: {result.trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
