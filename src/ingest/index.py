"""Phase 6 — upsert chunk embeddings into the Chroma vector store.

Reads ``data/chunks/*.jsonl`` (text + metadata) and ``data/embeddings/*.jsonl``
(vectors) and writes the collection configured in ``config.yaml`` under
``chroma.persist_dir``.

The embedder is **not** called here: vectors are loaded from the Phase 5 dump
and joined to chunks by ``chunk_id``. The collection stores, per chunk:

- id: ``chunk_id``
- embedding: the stored vector
- document: the chunk ``text``
- metadata: ``source_url``, ``title``, ``section``, ``fetched_at``,
  ``scheme_or_page_id``

Usage::

    python -m src.ingest.index            # create/update in place
    python -m src.ingest.index --rebuild  # delete and recreate the collection
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.config import Settings, load_settings
from src.ingest import chroma_client

CHUNK_FIELDS = (
    "chunk_id",
    "text",
    "source_url",
    "title",
    "section",
    "fetched_at",
    "scheme_or_page_id",
)
METADATA_FIELDS = (
    "source_url",
    "title",
    "section",
    "fetched_at",
    "scheme_or_page_id",
)
EMBEDDING_FIELDS = ("chunk_id", "model", "dim", "vector")
DEFAULT_BATCH_SIZE = 100


@dataclass(frozen=True)
class IndexSummary:
    """Outcome of one indexing run."""

    collection: str
    model: str
    chunks: int
    upserted: int
    pruned: int
    count: int


def _read_jsonl(path: Path) -> Iterable[tuple[int, Any]]:
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            yield line_number, json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc


def load_chunks(chunks_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load every chunk, validating the fields Chroma needs."""
    chunks: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()

    for path in sorted(chunks_dir.glob("*.jsonl")):
        for line_number, chunk in _read_jsonl(path):
            where = f"{path.name}:{line_number}"
            if not isinstance(chunk, dict):
                problems.append(f"{where}: chunk is not a JSON object")
                continue
            missing = [
                field
                for field in CHUNK_FIELDS
                if not isinstance(chunk.get(field), str) or not chunk[field].strip()
            ]
            if missing:
                problems.append(f"{where}: missing or empty {', '.join(missing)}")
                continue

            chunk_id = str(chunk["chunk_id"])
            if chunk_id in seen:
                problems.append(f"{where}: duplicate chunk_id {chunk_id!r}")
                continue
            seen.add(chunk_id)
            chunks.append(chunk)

    return chunks, problems


def load_embeddings(
    embeddings_dir: Path, expected_model: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load embedding records keyed by ``chunk_id``."""
    embeddings: dict[str, dict[str, Any]] = {}
    problems: list[str] = []

    for path in sorted(embeddings_dir.glob("*.jsonl")):
        for line_number, record in _read_jsonl(path):
            where = f"{path.name}:{line_number}"
            if not isinstance(record, dict):
                problems.append(f"{where}: embedding is not a JSON object")
                continue

            missing = [
                field for field in EMBEDDING_FIELDS if record.get(field) in (None, "")
            ]
            if missing:
                problems.append(f"{where}: missing {', '.join(missing)}")
                continue

            chunk_id = str(record["chunk_id"])
            if chunk_id in embeddings:
                problems.append(f"{where}: duplicate chunk_id {chunk_id!r}")
                continue

            if record["model"] != expected_model:
                problems.append(
                    f"{where}: model {record['model']!r} != {expected_model!r}"
                )
                continue

            dim = record["dim"]
            vector = record["vector"]
            if not isinstance(dim, int) or dim <= 0:
                problems.append(f"{where}: invalid dim {dim!r}")
                continue
            if not isinstance(vector, list) or len(vector) != dim:
                problems.append(
                    f"{where}: vector length "
                    f"{len(vector) if isinstance(vector, list) else 'n/a'} != {dim}"
                )
                continue

            embeddings[chunk_id] = record

    return embeddings, problems


def build_records(
    chunks_dir: Path, embeddings_dir: Path, expected_model: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Join chunks to embeddings and return ``(records, problems, warnings)``."""
    chunks, problems = load_chunks(chunks_dir)
    embeddings, embedding_problems = load_embeddings(
        embeddings_dir, expected_model
    )
    problems.extend(embedding_problems)

    chunk_ids = {str(chunk["chunk_id"]) for chunk in chunks}
    missing = sorted(chunk_ids - set(embeddings))
    for chunk_id in missing:
        problems.append(f"no embedding for chunk {chunk_id!r}")

    warnings = [
        f"ignoring embedding with no matching chunk: {chunk_id!r}"
        for chunk_id in sorted(set(embeddings) - chunk_ids)
    ]

    dims = {int(embeddings[chunk_id]["dim"]) for chunk_id in chunk_ids & set(embeddings)}
    if len(dims) > 1:
        problems.append(f"inconsistent embedding dims: {sorted(dims)}")

    records: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        record = embeddings.get(chunk_id)
        if record is None:
            continue
        records.append(
            {
                "id": chunk_id,
                "embedding": [float(value) for value in record["vector"]],
                "document": str(chunk["text"]),
                "metadata": {field: str(chunk[field]) for field in METADATA_FIELDS},
            }
        )

    return records, problems, warnings


def upsert_records(
    collection: Any, records: list[dict[str, Any]], batch_size: int
) -> int:
    """Upsert records in batches. Re-running with the same ids is idempotent."""
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        collection.upsert(
            ids=[record["id"] for record in batch],
            embeddings=[record["embedding"] for record in batch],
            documents=[record["document"] for record in batch],
            metadatas=[record["metadata"] for record in batch],
        )
    return len(records)


def prune_stale(
    collection: Any, wanted: set[str], batch_size: int = DEFAULT_BATCH_SIZE
) -> int:
    """Delete collection ids that no longer have a chunk, so count stays exact."""
    existing = collection.get(include=[])["ids"]
    stale = [chunk_id for chunk_id in existing if chunk_id not in wanted]
    for start in range(0, len(stale), batch_size):
        collection.delete(ids=stale[start : start + batch_size])
    return len(stale)


def index(
    settings: Settings,
    rebuild: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IndexSummary:
    """Validate the inputs, then upsert them into the configured collection."""
    chunks_dir = settings.project_root / "data" / "chunks"
    embeddings_dir = settings.project_root / "data" / "embeddings"

    records, problems, warnings = build_records(
        chunks_dir, embeddings_dir, settings.embedding_model
    )
    for warning in warnings:
        print(f"  warn {warning}")
    if problems:
        print(f"Cannot index: {len(problems)} problem(s)")
        for problem in problems[:20]:
            print(f"  {problem}")
        raise ValueError(
            f"{len(problems)} problem(s) in chunks/embeddings; collection untouched"
        )
    if not records:
        raise ValueError(f"No chunks with embeddings found in {chunks_dir}")

    client = chroma_client.get_client(settings.chroma_persist_dir)
    if rebuild:
        collection = chroma_client.rebuild_collection(
            client, settings.chroma_collection, settings.embedding_model
        )
        print(f"Recreated collection {settings.chroma_collection!r}")
    else:
        collection = chroma_client.get_or_create_collection(
            client, settings.chroma_collection, settings.embedding_model
        )

    upserted = upsert_records(collection, records, batch_size)
    pruned = prune_stale(collection, {record["id"] for record in records}, batch_size)

    return IndexSummary(
        collection=settings.chroma_collection,
        model=settings.embedding_model,
        chunks=len(records),
        upserted=upserted,
        pruned=pruned,
        count=collection.count(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upsert chunk embeddings into the Chroma vector store."
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete and recreate the collection before upserting.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Records per upsert/delete call (default {DEFAULT_BATCH_SIZE}).",
    )
    args = parser.parse_args(argv)

    if args.batch_size <= 0:
        print("--batch-size must be positive")
        return 1

    settings = load_settings()
    print(
        f"Indexing into collection {settings.chroma_collection!r} at "
        f"{settings.chroma_persist_dir} (model {settings.embedding_model})"
    )
    try:
        summary = index(settings, rebuild=args.rebuild, batch_size=args.batch_size)
    except ValueError as exc:
        print(str(exc))
        return 1

    print(
        f"  upserted {summary.upserted} record(s), pruned {summary.pruned} "
        f"stale id(s)"
    )
    print(
        f"Done: collection.count()={summary.count}, chunks={summary.chunks}, "
        f"model={summary.model}"
    )
    if summary.count != summary.chunks:
        print(
            f"Count mismatch: collection has {summary.count}, "
            f"expected {summary.chunks}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
