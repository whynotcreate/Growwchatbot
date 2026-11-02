"""Tests for automated ingestion and data refresh pipeline."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import load_settings
from src.ingest.chunk import chunk_all
from src.ingest.index import prune_stale
from src.ingest import chroma_client
from src.ingest.run import PipelineSummary, run_pipeline
from src.ingest.scrape import ScrapeResult, read_sources


def test_read_sources():
    """Verify read_sources loads all registered sources from urls.csv."""
    settings = load_settings()
    sources = read_sources(settings.sources_path)
    assert len(sources) >= 5
    ids = [s.doc_id for s in sources]
    assert "hdfc-amc" in ids
    assert "hdfc-large-cap-direct-growth" in ids
    assert "hdfc-mid-cap-direct-growth" in ids
    assert "hdfc-flexi-cap-direct-growth" in ids
    assert "hdfc-elss-tax-saver-direct-growth" in ids


def test_chunk_all(tmp_path):
    """Verify chunk_all generates valid structured chunks for all loaded docs in an isolated dir."""
    settings = load_settings()
    loaded_dir = settings.project_root / "data" / "loaded"
    chunks_dir = tmp_path / "chunks"

    loaded_files = list(loaded_dir.glob("*.json"))
    assert len(loaded_files) >= 5, "Expected at least 5 loaded document files"

    written, total, failed = chunk_all(loaded_dir, chunks_dir, settings)
    assert written == len(loaded_files), f"Expected {len(loaded_files)} written chunk files, got {written}"
    assert failed == 0, f"Expected 0 chunking failures, got {failed}"
    assert total > 0, "Expected non-empty total chunk count"

    # Invariant: Each document must produce non-empty chunks with required fields
    chunk_files = list(chunks_dir.glob("*.jsonl"))
    assert len(chunk_files) == len(loaded_files)
    for cf in chunk_files:
        lines = [
            line.strip()
            for line in cf.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) > 0, f"Chunk file {cf.name} is empty"
        first_chunk = json.loads(lines[0])
        assert "chunk_id" in first_chunk and first_chunk["chunk_id"]
        assert "text" in first_chunk and first_chunk["text"]
        assert "source_url" in first_chunk and first_chunk["source_url"].startswith("http")


def test_pipeline_summary_structure(tmp_path):
    """Verify run_pipeline executes and returns a complete, invariant PipelineSummary with isolated ChromaDB."""
    settings = load_settings()
    # Isolate ChromaDB to a temporary directory so the test is isolated and doesn't mutate data/chroma/
    isolated_settings = replace(
        settings,
        chroma_persist_dir=tmp_path / "chroma",
        chroma_collection="test_nextleap_mf_chunks",
    )
    # Execute incremental pipeline (refresh=False uses cached raw files)
    summary = run_pipeline(isolated_settings, refresh=False, rebuild=False)

    assert isinstance(summary, PipelineSummary)
    assert summary.sources_count >= 5
    assert summary.loaded_count == summary.sources_count
    assert summary.chunks_count > 0

    # Invariants: Every chunk must have an embedding
    assert summary.embeddings_count == summary.chunks_count, (
        f"Embeddings count ({summary.embeddings_count}) must match chunks count ({summary.chunks_count})"
    )
    # Invariant: ChromaDB vector count must match chunk count after upsert and stale pruning
    assert summary.chroma_count == summary.chunks_count, (
        f"ChromaDB vector count ({summary.chroma_count}) must match chunks count ({summary.chunks_count})"
    )
    # Invariant: All active chunk records joined with embeddings were submitted for upsert
    assert summary.upserted == summary.chunks_count
    # Invariant: Pruned count is non-negative and does not fail when stale records are legitimately removed
    assert summary.pruned >= 0


def test_prune_stale_removes_unwanted_vectors(tmp_path):
    """Verify prune_stale removes obsolete vectors when chunk count decreases on a refresh."""
    client = chroma_client.get_client(tmp_path / "chroma")
    collection = chroma_client.get_or_create_collection(
        client, "test_prune_collection", "sentence-transformers/all-MiniLM-L6-v2"
    )

    # Insert three vectors
    collection.upsert(
        ids=["chunk_1", "chunk_2", "stale_chunk_3"],
        embeddings=[[0.1] * 384, [0.2] * 384, [0.3] * 384],
        documents=["text 1", "text 2", "text 3"],
        metadatas=[{"source_url": "https://a.com"}, {"source_url": "https://b.com"}, {"source_url": "https://c.com"}],
    )
    assert collection.count() == 3

    # Prune keeping only chunk_1 and chunk_2
    pruned_count = prune_stale(collection, wanted={"chunk_1", "chunk_2"})
    assert pruned_count == 1
    assert collection.count() == 2
    remaining_ids = set(collection.get(include=[])["ids"])
    assert remaining_ids == {"chunk_1", "chunk_2"}
    assert "stale_chunk_3" not in remaining_ids


def test_scrape_failure_aborts_pipeline_before_indexing(monkeypatch):
    """Regression test: failed scrape aborts pipeline and protects ChromaDB from modification/pruning."""
    settings = load_settings()

    # Simulate one source failing during scrape
    mock_failed_scrape_result = [
        ScrapeResult("hdfc-amc", "https://...", "fetched", 200),
        ScrapeResult(
            "hdfc-large-cap-direct-growth",
            "https://...",
            "failed",
            500,
            "HTTP 500 Server Error",
        ),
    ]

    monkeypatch.setattr(
        "src.ingest.run.scrape_all", lambda *args, **kwargs: mock_failed_scrape_result
    )

    # Mock load_all and index to verify they are never called
    mock_load_all = MagicMock()
    mock_index = MagicMock()
    monkeypatch.setattr("src.ingest.run.load_all", mock_load_all)
    monkeypatch.setattr("src.ingest.run.index", mock_index)

    with pytest.raises(RuntimeError) as exc_info:
        run_pipeline(settings, refresh=True)

    err_text = str(exc_info.value)
    assert "Scrape failed for 1 source(s)" in err_text
    assert (
        "Aborting pipeline before loading, chunking, embedding or indexing"
        in err_text
    )
    assert "Existing ChromaDB database has been preserved" in err_text

    # Verify that load_all and ChromaDB indexing/pruning were NEVER invoked
    mock_load_all.assert_not_called()
    mock_index.assert_not_called()

