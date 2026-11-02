"""Tests for automated ingestion and data refresh pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import load_settings
from src.ingest.chunk import chunk_all
from src.ingest.run import PipelineSummary, run_pipeline
from src.ingest.scrape import read_sources


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


def test_chunk_all():
    """Verify chunk_all generates valid structured chunks for all loaded docs."""
    settings = load_settings()
    loaded_dir = settings.project_root / "data" / "loaded"
    chunks_dir = settings.project_root / "data" / "chunks"

    written, total, failed = chunk_all(loaded_dir, chunks_dir, settings)
    assert written >= 5
    assert total >= 64
    assert failed == 0


def test_pipeline_summary_structure():
    """Verify run_pipeline executes and returns a complete PipelineSummary."""
    settings = load_settings()
    # Execute incremental pipeline (refresh=False uses cached raw files)
    summary = run_pipeline(settings, refresh=False, rebuild=False)

    assert isinstance(summary, PipelineSummary)
    assert summary.sources_count >= 5
    assert summary.loaded_count >= 5
    assert summary.chunks_count >= 64
    assert summary.embeddings_count >= 64
    assert summary.chroma_count >= 64
    assert summary.pruned == 0
