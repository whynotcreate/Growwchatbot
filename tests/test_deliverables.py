"""Deliverable checks: README, sample Q&A, and secret hygiene."""

from __future__ import annotations

import re

from src.config import load_settings
from src.safety.pii import detect_pii

PROJECT_ROOT = load_settings().project_root


def _qa_sections() -> list[str]:
    text = (PROJECT_ROOT / "samples" / "qa.md").read_text(encoding="utf-8")
    return [
        section.strip()
        for section in re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]
        if section.strip()
    ]


def test_sample_qa_has_8_to_10_complete_entries():
    sections = _qa_sections()

    assert 8 <= len(sections) <= 10
    for section in sections:
        assert "**Question:**" in section
        assert "**Answer:**" in section
        assert "**Source:** https://" in section
        assert "**Last updated from sources:**" in section
        assert "**Status:**" in section


def test_sample_qa_contains_no_personal_data():
    text = (PROJECT_ROOT / "samples" / "qa.md").read_text(encoding="utf-8")
    assert detect_pii(text).detected is False


def test_readme_documents_setup_scope_deployment_and_limits():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()

    for required in (
        "## scope",
        "## safety and privacy",
        "## quick start",
        "## deployment",
        "streamlit secrets",
        "## known limitations",
        "facts-only. no investment advice.",
        "python -m src.ingest.run",
        "streamlit run src/ui/app.py",
    ):
        assert required in readme, required


def test_secret_hygiene_files():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert ".env" in gitignore.splitlines()
    assert "GROQ_API_KEY=\n" in example
    assert "sk-" not in example
    assert "gsk_" not in example
