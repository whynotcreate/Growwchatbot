"""NextLeap Ingestion Pipeline Runner.

Orchestrates the offline data ingestion and vector indexing phases in order:
1. Scrape (Phase 2): HTTP GET official pages into data/raw/
2. Load (Phase 3): Clean HTML into documents in data/loaded/
3. Chunk (Phase 4): Structure-aware chunking into data/chunks/
4. Embed (Phase 5): Generate vector embeddings in data/embeddings/
5. Index (Phase 6): Upsert embeddings and prune stale chunks in ChromaDB (data/chroma/)

Usage::

    python -m src.ingest.run            # update incrementally
    python -m src.ingest.run --refresh  # refetch all pages from source
    python -m src.ingest.run --refresh --rebuild  # refetch and rebuild ChromaDB
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import Settings, load_settings
from src.ingest.chunk import chunk_all
from src.ingest.embed import embed_all, validate_embeddings
from src.ingest.embedder import get_embedder
from src.ingest.index import index
from src.ingest.load import load_all
from src.ingest.scrape import read_sources, scrape_all


@dataclass(frozen=True)
class PipelineSummary:
    """Summary of complete ingestion pipeline run."""

    sources_count: int
    scraped_fetched: int
    scraped_skipped: int
    scraped_failed: int
    loaded_count: int
    chunks_count: int
    embeddings_count: int
    chroma_count: int
    upserted: int
    pruned: int


def run_pipeline(
    settings: Settings | None = None,
    *,
    refresh: bool = False,
    rebuild: bool = False,
) -> PipelineSummary:
    """Execute the full 5-stage ingestion and indexing pipeline."""
    settings = settings or load_settings()
    root = settings.project_root

    raw_dir = root / "data" / "raw"
    loaded_dir = root / "data" / "loaded"
    chunks_dir = root / "data" / "chunks"
    embeddings_dir = root / "data" / "embeddings"

    print("=" * 60)
    print("NextLeap Ingestion & Indexing Pipeline")
    print(f"Project root : {root}")
    print(f"Refresh mode : {refresh}")
    print(f"Rebuild index: {rebuild}")
    print("=" * 60)

    # Stage 1: Scrape
    print("\n[1/5] Scraping source pages...")
    sources = read_sources(settings.sources_path)
    scrape_results = scrape_all(sources, raw_dir, refresh=refresh)
    fetched = sum(1 for r in scrape_results if r.outcome == "fetched")
    skipped = sum(1 for r in scrape_results if r.outcome == "skipped")
    failed_scrapes = [r for r in scrape_results if r.outcome == "failed"]
    print(f"Scrape completed: {fetched} fetched, {skipped} skipped, {len(failed_scrapes)} failed.")

    if failed_scrapes:
        failed_ids = [r.doc_id for r in failed_scrapes]
        error_msg = (
            f"Scrape failed for {len(failed_scrapes)} source(s): {failed_ids}. "
            "Aborting pipeline before loading, chunking, embedding or indexing. "
            "Existing ChromaDB database has been preserved."
        )
        print(f"ERROR: {error_msg}")
        raise RuntimeError(error_msg)

    # Stage 2: Load
    print("\n[2/5] Cleaning and loading HTML documents...")
    load_results = load_all(sources, raw_dir, loaded_dir)
    loaded = sum(1 for r in load_results if r.outcome == "loaded")
    failed_loads = [r for r in load_results if r.outcome == "failed"]
    print(f"Load completed: {loaded} loaded, {len(failed_loads)} failed.")
    if loaded == 0:
        raise RuntimeError("No documents were loaded. Ingestion cannot proceed.")

    # Stage 3: Chunk
    print("\n[3/5] Chunking documents into retrieval units...")
    written_docs, total_chunks, failed_chunks = chunk_all(loaded_dir, chunks_dir, settings)
    print(f"Chunking completed: {written_docs} doc(s), {total_chunks} chunk(s), {failed_chunks} failed.")
    if total_chunks == 0:
        raise RuntimeError("No chunks were generated. Ingestion cannot proceed.")

    # Stage 4: Embed
    print(f"\n[4/5] Generating embeddings using {settings.embedding_model}...")
    embedder = get_embedder(settings.embedding_model)
    embed_results = embed_all(chunks_dir, embeddings_dir, embedder)
    total_embeddings = sum(r.embeddings for r in embed_results)
    failed_embeds = sum(1 for r in embed_results if r.outcome == "failed")
    print(f"Embeddings completed: {total_embeddings} embedding(s), {failed_embeds} failed.")

    on_disk_embeddings, problems = validate_embeddings(embeddings_dir, settings.embedding_model)
    if problems:
        raise RuntimeError(f"Embedding validation failed: {problems[:5]}")

    # Stage 5: Index in ChromaDB
    print(f"\n[5/5] Updating ChromaDB collection '{settings.chroma_collection}'...")
    index_summary = index(settings, rebuild=rebuild)
    print(f"ChromaDB completed: {index_summary.upserted} upserted, {index_summary.pruned} pruned, count={index_summary.count}.")

    print("\n" + "=" * 60)
    print(f"Pipeline finished successfully! ChromaDB index now holds {index_summary.count} vectors.")
    print("=" * 60)

    return PipelineSummary(
        sources_count=len(sources),
        scraped_fetched=fetched,
        scraped_skipped=skipped,
        scraped_failed=len(failed_scrapes),
        loaded_count=loaded,
        chunks_count=total_chunks,
        embeddings_count=total_embeddings,
        chroma_count=index_summary.count,
        upserted=index_summary.upserted,
        pruned=index_summary.pruned,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full NextLeap ingestion and vector indexing pipeline."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch pages from web sources even if cached.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete and rebuild the ChromaDB collection from scratch.",
    )
    args = parser.parse_args(argv)

    try:
        run_pipeline(refresh=args.refresh, rebuild=args.rebuild)
        return 0
    except Exception as exc:
        print(f"\nPipeline failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
