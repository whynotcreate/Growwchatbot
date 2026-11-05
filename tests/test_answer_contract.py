"""Answer-format, citation, date, and honest-not-found tests."""

from __future__ import annotations

import re

import pytest

from src.config import load_settings
from src.generate.answer import (
    NOT_FOUND_TEXT,
    generate_answer,
    not_found_answer,
    sanitize_text,
)
from src.retrieve.search import Hit, RetrievalResult, search


def _hit(
    chunk_id: str = "chunk-1",
    *,
    score: float = 0.9,
    section: str = "Expense Ratio",
    fetched_at: str = "2026-11-05T12:00:00Z",
) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        score=score,
        text="HDFC Large Cap Fund Direct Growth — Expense Ratio: 1.02%",
        source_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        title="HDFC Large Cap Fund Direct Growth",
        section=section,
        scheme_or_page_id="hdfc-large-cap-direct-growth",
        fetched_at=fetched_at,
    )


def _result(
    *,
    status: str = "ok",
    hits: tuple[Hit, ...] | None = None,
    citation_url: str | None = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    last_updated: str | None = "2026-11-05",
) -> RetrievalResult:
    return RetrievalResult(
        query_id="qid",
        asked_at="2026-11-05T12:00:00Z",
        question="What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        question_redacted=False,
        intent="factual",
        status=status,
        top_k=5,
        min_score=0.35,
        hits=hits if hits is not None else (_hit(),),
        citation_url=citation_url,
        last_updated_from_sources=last_updated,
    )


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def generate(self, system: str, user: str) -> str:
        self.calls += 1
        assert "only the retrieved chunks" in system.lower()
        assert "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth" in user
        return self.response


class ExplodingClient:
    def generate(self, system: str, user: str) -> str:  # pragma: no cover
        raise AssertionError("The LLM must not be called for empty retrieval")


def test_sanitize_strips_urls_citation_lines_and_limits_to_three_sentences():
    raw = (
        "Answer: The expense ratio is 1.02%. "
        "See https://evil.example/path and [this page](https://evil.example). "
        "Source: https://evil.example\n"
        "Last updated from sources: 1999-01-01\n"
        "It is charged annually. This is a fourth sentence. This is a fifth."
    )

    text = sanitize_text(raw)

    assert "http" not in text.lower()
    assert "source:" not in text.lower()
    assert "last updated" not in text.lower()
    assert "answer:" not in text.lower()
    assert len(re.findall(r"[.!?](?:\s|$)", text)) <= 3


def test_generate_answer_attaches_exactly_one_citation_and_real_date():
    settings = load_settings()
    client = FakeClient(
        "The expense ratio is 1.02%. It is charged annually. "
        "See https://evil.example for details."
    )

    answer = generate_answer("What is the expense ratio?", _result(), client=client)

    assert client.calls == 1
    assert answer["refused"] is False
    assert answer["citation_url"] == (
        "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    assert answer["last_updated_from_sources"] == "2026-11-05"
    assert "http" not in answer["text"].lower()
    assert answer["text"].count("1.02%") == 1


def test_not_found_uses_fallback_citation_without_calling_llm():
    result = _result(
        status="not_found",
        hits=(),
        citation_url="https://groww.in/mutual-funds/amc/hdfc-mutual-funds",
        last_updated=None,
    )

    answer = generate_answer("Anything?", result, client=ExplodingClient())

    assert answer == not_found_answer(result)
    assert answer["text"] == NOT_FOUND_TEXT
    assert answer["refused"] is False
    assert answer["last_updated_from_sources"] is None
    assert answer["citation_url"].startswith("https://groww.in/")


def test_low_score_real_retrieval_is_honest_not_found():
    result = search(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        min_score=1.0,
        write_trace=False,
    )

    assert result.status == "not_found"
    assert result.hits == ()
    assert result.citation_url == "https://groww.in/mutual-funds/amc/hdfc-mutual-funds"
    assert result.last_updated_from_sources is None


def test_real_retrieval_cites_the_scheme_page_and_uses_ingested_date():
    result = search(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        write_trace=False,
    )

    assert result.status == "ok"
    assert result.hits
    assert result.citation_url == result.hits[0].source_url
    assert result.citation_url == (
        "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    assert result.last_updated_from_sources is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result.last_updated_from_sources)
    assert "1.02%" in result.hits[0].text


def test_answer_citation_is_not_a_url_list():
    settings = load_settings()
    answer = generate_answer(
        "What is the expense ratio?",
        _result(),
        client=FakeClient("The expense ratio is 1.02%."),
        settings=settings,
    )

    citation = answer["citation_url"]
    assert isinstance(citation, str)
    assert citation.startswith("https://")
    assert "," not in citation
    assert " " not in citation
