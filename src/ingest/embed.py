"""Phase 5 — embed chunk text into ``data/embeddings``.

Reads ``data/chunks/*.jsonl`` only. For every chunk it writes one record:

.. code-block:: json

    {"chunk_id": "...", "model": "...", "dim": 384, "vector": [...]}

The dump is one JSONL file per chunk document, mirroring ``data/chunks``.
Chunk metadata stays in ``data/chunks`` and is joined later by ``chunk_id``.
This phase does not open Chroma or write ``data/chroma``.

Usage::

    python -m src.ingest.embed
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.config import load_settings
from src.ingest.embedder import Embedder, get_embedder

REQUIRED_CHUNK_FIELDS = ("chunk_id", "text")
EMBEDDING_FIELDS = ("chunk_id", "model", "dim", "vector")


@dataclass(frozen=True)
class EmbedResult:
    """Outcome for one chunk document."""

    doc_id: str
    chunks: int
    embeddings: int
    model: str
    dim: int
    outcome: str
    reason: str | None = None


def read_chunks(path: Path) -> list[dict[str, object]]:
    """Read and validate the fields needed from one chunk JSONL file."""
    chunks: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(chunk, dict):
            raise ValueError(f"{path}:{line_number}: chunk is not a JSON object")

        for field in REQUIRED_CHUNK_FIELDS:
            value = chunk.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{path}:{line_number}: missing or empty {field!r}"
                )
        chunks.append(chunk)
    return chunks


def embed_chunks(
    chunks: Sequence[dict[str, object]], embedder: Embedder
) -> list[dict[str, object]]:
    """Embed chunk texts and build the embedding records."""
    vectors = embedder.encode([str(chunk["text"]) for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError(
            f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
        )

    records: list[dict[str, object]] = []
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != embedder.dimension:
            raise ValueError(
                f"chunk {chunk['chunk_id']!r}: expected dim "
                f"{embedder.dimension}, got {len(vector)}"
            )
        records.append(
            {
                "chunk_id": str(chunk["chunk_id"]),
                "model": embedder.model_name,
                "dim": embedder.dimension,
                "vector": [float(value) for value in vector],
            }
        )
    return records


def write_embeddings(
    path: Path, records: Sequence[dict[str, object]]
) -> None:
    """Write one embedding record per line."""
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            ordered = {field: record[field] for field in EMBEDDING_FIELDS}
            handle.write(json.dumps(ordered, ensure_ascii=False) + "\n")


def embed_all(
    chunks_dir: Path,
    embeddings_dir: Path,
    embedder: Embedder,
) -> list[EmbedResult]:
    """Embed every chunk document, writing one JSONL dump per document."""
    embeddings_dir.mkdir(parents=True, exist_ok=True)

    paths = sorted(chunks_dir.glob("*.jsonl"))
    results: list[EmbedResult] = []
    for path in paths:
        doc_id = path.stem
        chunks: list[dict[str, object]] = []
        try:
            chunks = read_chunks(path)
            records = embed_chunks(chunks, embedder)
            output_path = embeddings_dir / f"{doc_id}.jsonl"
            write_embeddings(output_path, records)

            results.append(
                EmbedResult(
                    doc_id=doc_id,
                    chunks=len(chunks),
                    embeddings=len(records),
                    model=embedder.model_name,
                    dim=embedder.dimension,
                    outcome="embedded",
                )
            )
            print(
                f"  ok   {doc_id}: {len(records)} embedding(s), "
                f"dim={embedder.dimension}"
            )
        except Exception as exc:  # keep one bad document from stopping the rest
            reason = f"{type(exc).__name__}: {exc}"
            results.append(
                EmbedResult(
                    doc_id=doc_id,
                    chunks=len(chunks),
                    embeddings=0,
                    model=embedder.model_name,
                    dim=embedder.dimension,
                    outcome="failed",
                    reason=reason,
                )
            )
            print(f"  fail {doc_id}: {reason}")

    return results


def validate_embeddings(
    embeddings_dir: Path, expected_model: str
) -> tuple[int, list[str]]:
    """Check the on-disk dump and return ``(record_count, problems)``."""
    count = 0
    problems: list[str] = []
    for path in sorted(embeddings_dir.glob("*.jsonl")):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{path.name}:{line_number}: invalid JSON: {exc}")
                continue
            if not isinstance(record, dict):
                problems.append(f"{path.name}:{line_number}: not a JSON object")
                continue

            count += 1
            if record.get("model") != expected_model:
                problems.append(
                    f"{path.name}:{line_number}: model {record.get('model')!r} "
                    f"!= {expected_model!r}"
                )
            dim = record.get("dim")
            vector = record.get("vector")
            if not isinstance(dim, int) or dim <= 0:
                problems.append(f"{path.name}:{line_number}: invalid dim {dim!r}")
            elif not isinstance(vector, list) or len(vector) != dim:
                problems.append(
                    f"{path.name}:{line_number}: vector length "
                    f"{len(vector) if isinstance(vector, list) else 'n/a'} != {dim}"
                )
    return count, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed chunk text into data/embeddings/."
    )
    parser.parse_args(argv)

    settings = load_settings()
    chunks_dir = settings.project_root / "data" / "chunks"
    embeddings_dir = settings.project_root / "data" / "embeddings"

    if not chunks_dir.is_dir():
        print(f"Chunks directory not found: {chunks_dir}")
        return 1

    paths = sorted(chunks_dir.glob("*.jsonl"))
    if not paths:
        print(f"No chunk files in {chunks_dir}")
        return 1

    print(
        f"Embedding {len(paths)} chunk document(s) with "
        f"{settings.embedding_model} -> {embeddings_dir}"
    )
    try:
        embedder = get_embedder()
    except Exception as exc:
        print(f"Could not load embedding model: {type(exc).__name__}: {exc}")
        return 1

    results = embed_all(chunks_dir, embeddings_dir, embedder)

    embedded = sum(result.outcome == "embedded" for result in results)
    failed = sum(result.outcome == "failed" for result in results)
    total_chunks = sum(result.chunks for result in results)
    total_embeddings = sum(result.embeddings for result in results)
    on_disk_count, problems = validate_embeddings(
        embeddings_dir, settings.embedding_model
    )

    print(
        f"Done: {embedded} document(s), {total_embeddings} embedding(s) "
        f"from {total_chunks} chunk(s), {failed} failed"
    )
    if on_disk_count != total_chunks:
        print(
            f"Count mismatch: {on_disk_count} embedding(s) on disk for "
            f"{total_chunks} chunk(s)"
        )
    if problems:
        print(f"Embedding dump problems: {len(problems)}")
        for problem in problems[:10]:
            print(f"  {problem}")

    return 1 if failed or on_disk_count != total_chunks or problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
