"""Tests for Phase 10 UI helpers and integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import load_settings
from src.generate.answer import GenerationError
from src.query import answer_question
from src.ui.app import (
    EXAMPLE_QUESTIONS,
    check_chroma_status,
    get_chunk_files,
    get_latest_retrieval_trace,
    get_loaded_files,
)


def test_example_questions_defined():
    """Verify three example questions are defined as per PRD/architecture."""
    assert len(EXAMPLE_QUESTIONS) == 3
    assert any("expense ratio" in q.lower() for q in EXAMPLE_QUESTIONS)
    assert any("elss" in q.lower() for q in EXAMPLE_QUESTIONS)
    assert any("capital-gains" in q.lower() for q in EXAMPLE_QUESTIONS)


def test_get_loaded_files():
    """Verify get_loaded_files finds all JSON documents in data/loaded."""
    settings = load_settings()
    loaded = get_loaded_files(settings.project_root)
    assert len(loaded) >= 5
    filenames = [p.name for p in loaded]
    assert "hdfc-large-cap-direct-growth.json" in filenames
    assert "hdfc-amc.json" in filenames


def test_get_chunk_files():
    """Verify get_chunk_files finds all JSONL files in data/chunks."""
    settings = load_settings()
    chunk_files = get_chunk_files(settings.project_root)
    assert len(chunk_files) >= 5
    filenames = [p.name for p in chunk_files]
    assert "hdfc-large-cap-direct-growth.jsonl" in filenames


def test_get_latest_retrieval_trace():
    """Verify get_latest_retrieval_trace retrieves a valid trace dictionary."""
    settings = load_settings()
    trace_file, trace_data = get_latest_retrieval_trace(settings.project_root)
    assert trace_file is not None
    assert trace_data is not None
    assert "query_id" in trace_data
    assert "hits" in trace_data
    assert "status" in trace_data


def test_check_chroma_status():
    """Verify check_chroma_status returns correct chunk count for existing index."""
    settings = load_settings()
    ready, count, desc = check_chroma_status(settings)
    assert ready is True
    assert count > 0
    assert "chunks" in desc


def test_check_chroma_status_missing():
    """Verify check_chroma_status handles invalid persist directory gracefully."""
    settings = load_settings()
    # Create fake settings pointing to non-existent collection
    fake_settings = MagicMock()
    fake_settings.chroma_persist_dir = settings.chroma_persist_dir
    fake_settings.chroma_collection = "non_existent_collection_12345"
    fake_settings.embedding_model = settings.embedding_model

    ready, count, desc = check_chroma_status(fake_settings)
    assert ready is False
    assert count == 0


def test_advice_refusal():
    """Verify advice question returns refusal answer directly without calling LLM."""
    ans = answer_question("Should I buy HDFC Mid Cap?")
    assert ans["refused"] is True
    assert ans["refusal_reason"] == "advice"
    assert "https://groww.in/mutual-funds/amc/hdfc-mutual-funds" in ans["citation_url"]


def test_pii_refusal():
    """Verify PII in question returns refusal answer directly without calling LLM."""
    ans = answer_question("My PAN is ABCDE1234F, what is exit load?")
    assert ans["refused"] is True
    assert ans["refusal_reason"] == "pii"


def test_forced_empty_retrieval():
    """Verify min_score=1.0 triggers deterministic not-found response without LLM."""
    ans = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        min_score=1.0,
    )
    assert ans["refused"] is False
    assert "couldn't find" in ans["text"].lower()
    assert ans["citation_url"] == "https://groww.in/mutual-funds/amc/hdfc-mutual-funds"


def test_groq_failure_raises_generation_error_when_key_missing():
    """Verify missing GROQ_API_KEY raises GenerationError without faking an answer."""
    # Ensure GROQ_API_KEY is not in settings
    settings = load_settings()
    if not settings.groq_api_key:
        with pytest.raises(GenerationError) as exc_info:
            answer_question(
                "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
                settings=settings,
            )
        assert "GROQ_API_KEY is not set" in str(exc_info.value)
