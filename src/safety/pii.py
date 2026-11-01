"""Phase 7 — PII detection for the safety gate.

Runs before retrieval. When ``detect_pii`` reports a hit the caller must refuse
and must **not** persist the raw question. ``redact_pii`` is provided so any
trace or log can keep the intent without keeping the personal data.

Detected kinds: email, PAN, Aadhaar, OTP, phone, and long account numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters for redaction: replace the most specific patterns first so the
# generic account-number rule does not swallow only part of an email/PAN/etc.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        "pan",
        re.compile(
            r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])"
        ),
    ),
    (
        "aadhaar",
        re.compile(
            r"(?<!\d)(?:\d{4}[\s-]\d{4}[\s-]\d{4}|\d{12})(?!\d)"
        ),
    ),
    (
        "otp",
        re.compile(
            r"(?:\botp\b|one[\s-]?time\s+password)[^\d\n]{0,15}\d{4,8}\b"
            r"|\b\d{4,8}\b[^\d\n]{0,10}\b(?:otp|one[\s-]?time\s+password)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{9}(?!\d)"),
    ),
    (
        "phone",
        re.compile(
            r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]\d{5}(?!\d)"
        ),
    ),
    ("account_number", re.compile(r"(?<!\d)\d{9,18}(?!\d)")),
)


@dataclass(frozen=True)
class PIIResult:
    """Outcome of scanning one question."""

    detected: bool
    kinds: tuple[str, ...]
    redacted: str


def detect_pii(text: str) -> PIIResult:
    """Scan ``text`` for personal data and return a redacted copy."""
    kinds = tuple(
        dict.fromkeys(kind for kind, pattern in _PATTERNS if pattern.search(text))
    )

    redacted = text
    for kind, pattern in _PATTERNS:
        redacted = pattern.sub(f"[REDACTED_{kind.upper()}]", redacted)

    return PIIResult(detected=bool(kinds), kinds=kinds, redacted=redacted)


def redact_pii(text: str) -> str:
    """Return ``text`` with every detected personal-data span replaced."""
    return detect_pii(text).redacted


def contains_pii(text: str) -> bool:
    """Convenience wrapper for callers that only need a yes/no answer."""
    return detect_pii(text).detected
