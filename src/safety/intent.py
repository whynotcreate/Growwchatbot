"""Phase 7 — intent classification and the safety gate.

Runs **before** retrieval and returns either:

- ``allow``: the trimmed question continues to retrieval, or
- ``refuse``: a complete answer object (no retrieval, no LLM).

The gate never searches Chroma, never calls Groq, and never writes the question
to disk. PII hits do not even pass the raw question back to the caller.

Rules are deliberately conservative and data-driven where possible: refusal
links come from ``sources/urls.csv`` (``educational`` / ``factsheet`` / ``amc``
rows), so expanding the source list automatically improves the copy.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.config import load_settings
from src.safety.pii import detect_pii

Intent = Literal[
    "empty", "pii", "advice", "performance", "out_of_corpus", "factual"
]
Action = Literal["allow", "refuse"]

FALLBACK_AMC_URL = "https://groww.in/mutual-funds/amc/hdfc-mutual-funds"

# Words in scheme titles that do not identify a scheme on their own.
_TITLE_STOPWORDS = {
    "hdfc",
    "fund",
    "funds",
    "direct",
    "plan",
    "growth",
    "mutual",
    "the",
    "and",
    "of",
    "for",
}

_ADVICE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "Should I buy/sell/invest/allocate ..."
        r"\bshould\s+(?:i|we)\s+"
        r"(?:buy|sell|hold|invest|switch|redeem|allocate|choose|pick|"
        r"start|stop|continue|exit|stay|wait)\b",
        # "Should I ... fund / scheme / SIP / market ..."
        r"\bshould\s+(?:i|we)\b[^.?!]{0,60}\b"
        r"(?:fund|scheme|nav|market|portfolio|sip|elss|mutual\s+fund)\b",
        # "Is HDFC ELSS good/suitable/right for me?"
        r"\b(?:is|are|would)\s+[^.?!]{0,40}\b"
        r"(?:good|suitable|right|safe|better|best)\s+for\s+(?:me|us)\b",
        r"\b(?:good|suitable|right|better|best)\s+for\s+(?:me|us)\b",
        # "Sell HDFC Large Cap now?"
        r"\b(?:buy|sell|hold)\b[^.?!]{0,30}\b"
        r"(?:now|today|instead|better|recommend|advice)\b",
        r"\b(?:is|would)\s+it\s+(?:a\s+)?good\s+(?:idea|time)\s+to\s+"
        r"(?:buy|sell|hold|invest)\b",
        r"\ballocat(?:e|ion)\b",
        r"\bportfolio\b",
        r"\brebalanc(?:e|ing)\b",
        r"\b(?:recommend|recommendation|advice|advisable|suggestion)\b",
        r"\bmarket\s+timing\b|\btime\s+the\s+market\b",
        r"\bwhen\s+should\s+(?:i|we)\s+"
        r"(?:buy|sell|invest|switch|redeem)\b",
        r"\bworth\s+(?:investing|buying)\b",
        r"\bwhich\s+(?:fund|scheme)\s+should\s+(?:i|we)\b",
    )
)

_PERFORMANCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcagr\b",
        r"\bxirr\b",
        r"\b(?:annualized|annualised|absolute|trailing|rolling)\s+returns?\b",
        r"\breturns?\b",
        r"\bperformance\b",
        r"\bperformed\b",
        r"\boutperform(?:ed|ing|s)?\b",
        r"\bbeat(?:en)?\b",
        r"\bcompare\b|\bcomparison\b",
        r"\bwhich\b[^.?!]{0,40}\b(?:fund|scheme)\b[^.?!]{0,40}\b"
        r"(?:better|best|worst)\b",
        r"\b(?:better|best|worst)\b[^.?!]{0,30}\b(?:fund|scheme)\b",
        r"\btop\s+performing\b",
        r"\bnav\s+history\b",
        r"\b(?:1|3|5|10)\s*-?\s*year\s+returns?\b",
    )
)

_TAX_RETURN_RE = re.compile(
    r"\btax\s+returns?\b|\breturns?\s+filing\b", re.IGNORECASE
)

_OUT_OF_CORPUS_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Other AMCs / banks are outside this demo's HDFC-only corpus.
        r"\b(?:sbi|icici|axis|kotak|nippon|aditya\s+birla|uti|dsp|mirae|"
        r"franklin|ppfas|parag\s+parikh|motilal\s+oswal|bandhan|"
        r"canara\s+robeco|idfc|invesco|sundaram|baroda|hsbc|pgim|"
        r"edelweiss|jm\s+financial|shriram|whiteoak|360\s+one)\b",
        r"\btata\s+(?:mutual|amc)\b",
        r"\bhdfc\s+bank\b|\bfixed\s+deposit\b",
        r"\blog\s?in\b|\bsign\s?in\b|\bpassword\b|\baccount\s+balance\b",
        r"\bpredict\b[^.?!]{0,30}\bnav\b|\bfuture\s+nav\b",
        r"\bnext\s+(?:week|month|year)\b[^.?!]{0,30}\bnav\b",
        r"\bguaranteed\b|\bassured\s+returns?\b",
    )
)

_REFUSAL_TEXT = {
    "empty": (
        "Please ask a question about HDFC Mutual Fund schemes, such as "
        "expense ratio, exit load, minimum SIP, or ELSS lock-in."
    ),
    "pii": (
        "Please don't share personal information such as PAN, Aadhaar, phone "
        "numbers, emails, OTPs, or account numbers. I can't accept or store "
        "those details, so please rephrase your question without them."
    ),
    "advice": (
        "I can only share facts from official HDFC Mutual Fund pages and can't "
        "give investment advice, recommendations, or portfolio guidance. For "
        "general investor education, see the official page linked here."
    ),
    "performance": (
        "I can't calculate or compare returns, CAGR, or performance rankings. "
        "The official scheme or factsheet page linked here has the published "
        "performance information."
    ),
    "out_of_corpus": (
        "I can only answer from the HDFC Mutual Fund pages in this demo's "
        "sources. For other AMCs or account actions, please use the official "
        "page linked here."
    ),
}


@dataclass(frozen=True)
class GuardrailResult:
    """Decision for one raw question."""

    action: Action
    intent: Intent
    question: str | None
    answer: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action, "intent": self.intent}
        if self.question is not None:
            payload["question"] = self.question
        if self.answer is not None:
            payload["answer"] = self.answer
        return payload


def _matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _is_performance(text: str) -> bool:
    # "Tax return" is a document, not fund performance.
    cleaned = _TAX_RETURN_RE.sub(" ", text)
    return _matches(cleaned, _PERFORMANCE_PATTERNS)


def classify_intent(question: str) -> Intent:
    """Classify a raw question. PII wins over every other intent."""
    if not question or not question.strip():
        return "empty"
    if detect_pii(question).detected:
        return "pii"
    if _matches(question, _ADVICE_PATTERNS):
        return "advice"
    if _is_performance(question):
        return "performance"
    if _matches(question, _OUT_OF_CORPUS_PATTERNS):
        return "out_of_corpus"
    return "factual"


def _resolve_sources_path(sources_path: str | Path | None) -> Path:
    if sources_path is not None:
        return Path(sources_path)
    try:
        return load_settings().sources_path
    except Exception:
        return Path(__file__).resolve().parents[2] / "sources" / "urls.csv"


def _load_sources(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("url")]


def _first_url(rows: list[dict[str, str]], page_type: str) -> str | None:
    for row in rows:
        if str(row.get("page_type", "")).strip().lower() == page_type:
            url = str(row.get("url", "")).strip()
            if url:
                return url
    return None


def _match_scheme_url(question: str, rows: list[dict[str, str]]) -> str | None:
    """Pick the scheme page mentioned in the question, if any."""
    lowered = question.lower()
    best_url: str | None = None
    best_score = 0

    for row in rows:
        if str(row.get("page_type", "")).strip().lower() != "scheme":
            continue
        tokens = [
            token
            for token in re.findall(r"[a-z]+", str(row.get("title", "")).lower())
            if token not in _TITLE_STOPWORDS
        ]
        score = sum(
            1
            for token in tokens
            if re.search(rf"\b{re.escape(token)}\b", lowered)
        )
        if score > best_score:
            best_score = score
            best_url = str(row.get("url", "")).strip() or None

    return best_url


def _refusal_url(
    intent: Intent, question: str, rows: list[dict[str, str]]
) -> str | None:
    if intent == "empty":
        return None

    amc = _first_url(rows, "amc") or (
        str(rows[0]["url"]).strip() if rows else FALLBACK_AMC_URL
    )

    if intent == "advice":
        return _first_url(rows, "educational") or amc
    if intent == "performance":
        return (
            _match_scheme_url(question, rows)
            or _first_url(rows, "factsheet")
            or amc
        )
    # PII and out-of-corpus questions still get one official page.
    return amc


def check_question(
    question: str, sources_path: str | Path | None = None
) -> GuardrailResult:
    """Run the Phase 7 gate on a raw question.

    ``allow`` results pass the trimmed question onward. ``refuse`` results
    carry a complete answer object and drop the question so callers cannot
    accidentally persist PII or retrieve for a refused intent.
    """
    intent = classify_intent(question)

    if intent == "factual":
        return GuardrailResult(
            action="allow",
            intent=intent,
            question=question.strip(),
            answer=None,
        )

    rows = _load_sources(_resolve_sources_path(sources_path))
    answer = {
        "text": _REFUSAL_TEXT[intent],
        "citation_url": _refusal_url(intent, question, rows),
        "last_updated_from_sources": None,
        "refused": True,
        "refusal_reason": intent,
    }
    return GuardrailResult(
        action="refuse", intent=intent, question=None, answer=answer
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Phase 7 safety gate on one question."
    )
    parser.add_argument("--q", required=True, help="Raw user question.")
    parser.add_argument(
        "--sources",
        default=None,
        help="Optional path to sources/urls.csv for refusal links.",
    )
    args = parser.parse_args(argv)

    result = check_question(args.q, args.sources)
    print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
