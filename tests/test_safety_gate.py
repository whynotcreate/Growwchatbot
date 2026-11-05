"""Rule-based safety gate tests, including known false positives/negatives."""

from __future__ import annotations

import json

import pytest

from src.config import load_settings
from src.query import answer_question
from src.retrieve.search import search
from src.safety.intent import check_question, classify_intent
from src.safety.pii import detect_pii

FACTUAL = [
    "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
    "Compare the expense ratio of HDFC Large Cap and HDFC Mid Cap.",
    "What is the exit load of HDFC Flexi Cap Fund?",
    "How do I get tax proof for Section 80C?",
    "What is the tax return document needed for ELSS?",
    "What is the minimum SIP for HDFC ELSS Tax Saver Fund?",
    "What are the riskometer levels?",
]

ADVICE = [
    "Should I buy HDFC Mid Cap?",
    "Which is better, HDFC Large Cap or HDFC Mid Cap?",
    "Is HDFC ELSS a good fund for me?",
    "Suggest a mutual fund for tax saving.",
    "Is now a good time to invest?",
    "How should I allocate my portfolio?",
    "Can you recommend a scheme?",
]

PERFORMANCE = [
    "What are the 3-year returns of HDFC Large Cap?",
    "Compare returns of HDFC Large Cap and Mid Cap.",
    "What is the CAGR of HDFC Flexi Cap?",
    "Which fund has the best performance?",
]

OUT_OF_CORPUS = [
    "What is the expense ratio of SBI Bluechip Fund?",
    "Will the market go up next month?",
    "What is my HDFC Bank account balance?",
    "What is the NAV prediction for next year?",
    "Can you guarantee returns?",
]

PII = [
    "My PAN is ABCDE1234F",
    "Call me on 9876543210",
    "Email me at a@b.com",
    "My Aadhaar is 1234 5678 9012",
    "OTP is 123456",
]


@pytest.mark.parametrize("question", FACTUAL)
def test_factual_questions_are_allowed(question: str):
    assert classify_intent(question) == "factual"
    result = check_question(question)
    assert result.action == "allow"
    assert result.question == question
    assert result.answer is None


@pytest.mark.parametrize("question", ADVICE)
def test_advice_is_refused(question: str):
    assert classify_intent(question) == "advice"
    result = check_question(question)
    assert result.action == "refuse"
    assert result.question is None
    assert result.answer is not None
    assert result.answer["refused"] is True
    assert result.answer["refusal_reason"] == "advice"
    assert result.answer["citation_url"].startswith("https://")


@pytest.mark.parametrize("question", PERFORMANCE)
def test_performance_is_refused(question: str):
    assert classify_intent(question) == "performance"
    result = check_question(question)
    assert result.action == "refuse"
    assert result.answer is not None
    assert result.answer["refusal_reason"] == "performance"
    # A performance refusal links to an official scheme/factsheet page.
    assert result.answer["citation_url"].startswith("https://")


@pytest.mark.parametrize("question", OUT_OF_CORPUS)
def test_out_of_corpus_is_refused(question: str):
    assert classify_intent(question) == "out_of_corpus"
    result = check_question(question)
    assert result.action == "refuse"
    assert result.answer is not None
    assert result.answer["refusal_reason"] == "out_of_corpus"


@pytest.mark.parametrize("question", PII)
def test_pii_wins_and_is_not_echoed(question: str):
    assert classify_intent(question) == "pii"
    result = check_question(question)
    assert result.action == "refuse"
    assert result.question is None
    assert result.answer is not None
    assert result.answer["refusal_reason"] == "pii"

    serialized = json.dumps(result.as_dict()).lower()
    for token in ("abcde1234f", "9876543210", "a@b.com", "1234 5678 9012", "123456"):
        assert token not in serialized


def test_pii_detector_does_not_flag_ordinary_fund_facts():
    for question in FACTUAL + PERFORMANCE:
        assert detect_pii(question).detected is False, question


def test_pii_question_writes_no_retrieval_trace(tmp_path):
    """The gate must discard a PII question before retrieval persistence."""
    answer = answer_question(
        "My PAN is ABCDE1234F and my phone is 9876543210. What is the exit load?",
        retrieval_dir=tmp_path,
    )

    assert answer["refused"] is True
    assert answer["refusal_reason"] == "pii"
    assert list(tmp_path.iterdir()) == []


def test_tax_return_document_is_not_treated_as_performance():
    # Regression: "tax return" is a document, not a fund return.
    assert classify_intent("Which document is the tax return for ELSS?") == "factual"


def test_expense_ratio_comparison_is_a_factual_question():
    # Regression: the old broad "compare" rule falsely refused this.
    question = "Compare the expense ratio of HDFC Large Cap and HDFC Flexi Cap."
    assert classify_intent(question) == "factual"


def test_direct_pii_retrieval_trace_stores_only_redacted_question(tmp_path):
    """Even if a caller bypasses the gate, search() must not persist PII."""

    class FakeEmbedder:
        def encode(self, texts):
            return [[0.0, 0.0, 0.0] for _ in texts]

    class EmptyCollection:
        def query(self, **kwargs):
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

    settings = load_settings()
    raw_question = "My PAN is ABCDE1234F and my email is person@example.com"
    result = search(
        raw_question,
        settings=settings,
        embedder=FakeEmbedder(),
        collection=EmptyCollection(),
        retrieval_dir=tmp_path,
        write_trace=True,
    )

    trace_files = list(tmp_path.glob("*.json"))
    assert len(trace_files) == 1
    payload = trace_files[0].read_text(encoding="utf-8")

    assert "ABCDE1234F" not in payload
    assert "person@example.com" not in payload
    assert "REDACTED_PAN" in payload
    assert "REDACTED_EMAIL" in payload
    assert result.question_redacted is True
    assert result.status == "not_found"
