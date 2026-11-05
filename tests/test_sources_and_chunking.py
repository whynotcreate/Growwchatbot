"""Source-list and ingestion-quality tests.

These tests guard the expanded 15–25 URL corpus and the loader/chunker fixes
that keep short FAQ answers while dropping link-only navigation boilerplate.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from src.config import load_settings
from src.ingest.chunk import MIN_GENERIC_CHUNK_CHARS, build_chunks
from src.ingest.load import load_document
from src.ingest.scrape import Source, read_sources

ALLOWED_HOSTS = {
    "groww.in",
    "www.groww.in",
    "investor.sebi.gov.in",
    "amfiindia.com",
    "www.amfiindia.com",
    "mutualfundssahihai.com",
    "www.mutualfundssahihai.com",
}

REQUIRED_SCHEMES = {
    "hdfc-large-cap-direct-growth",
    "hdfc-flexi-cap-direct-growth",
    "hdfc-elss-tax-saver-direct-growth",
    "hdfc-mid-cap-direct-growth",
}


def test_source_list_is_15_to_25_unique_official_https_urls():
    settings = load_settings()
    sources = read_sources(settings.sources_path)

    assert 15 <= len(sources) <= 25
    assert len({source.url for source in sources}) == len(sources)
    assert len({source.doc_id for source in sources}) == len(sources)

    for source in sources:
        parsed = urlparse(source.url)
        assert parsed.scheme == "https", source.url
        assert parsed.hostname in ALLOWED_HOSTS, source.url
        assert source.page_type in {
            "scheme",
            "amc",
            "howto",
            "educational",
            "factsheet",
        }
        assert source.title.strip()
        assert source.doc_id.strip()

    # HDFC AMC's own site blocks automated requests (HTTP 403), so it must not
    # appear as a source we cannot verify.
    assert all("hdfcfund.com" not in source.url for source in sources)


def test_source_list_covers_amc_and_four_schemes():
    settings = load_settings()
    sources = read_sources(settings.sources_path)
    ids = {source.doc_id for source in sources}
    types = {source.page_type for source in sources}

    assert REQUIRED_SCHEMES <= ids
    assert "hdfc-amc" in ids
    assert "amc" in types
    assert "educational" in types
    assert "howto" in types


def test_source_csv_keeps_exact_required_schema():
    settings = load_settings()
    header = settings.sources_path.read_text(encoding="utf-8").splitlines()[0]

    assert header == "doc_id,page_type,title,url"


def _write_raw(tmp_path, source: Source, html: str) -> None:
    (tmp_path / f"{source.doc_id}.meta.json").write_text(
        json.dumps(
            {
                "doc_id": source.doc_id,
                "url": source.url,
                "fetched_at": "2026-11-05T12:00:00Z",
                "http_status": 200,
                "content_type": "text/html",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{source.doc_id}.html").write_text(html, encoding="utf-8")


def test_loader_keeps_short_answer_inside_link_heavy_wrapper(tmp_path):
    """A footer/nav wrapper must not swallow the short FAQ answer it contains."""
    source = Source(
        "faq",
        "howto",
        "FAQ",
        "https://groww.in/help/example",
    )
    answer = (
        "You can request your tax proof for Section 80C by downloading the "
        "Mutual Fund-ELSS statement from your profile. This answer must stay."
    )
    links = " ".join(
        f'<a href="https://groww.in/nav/{index}">Navigation link {index}</a>'
        for index in range(60)
    )
    # The wrapper has high link density but >80 chars of non-link prose.
    _write_raw(
        tmp_path,
        source,
        f"<html><body><div id='root'><p>{answer}</p>"
        f"<div id='links'>{links}</div></div></body></html>",
    )

    document, reason = load_document(source, tmp_path)

    assert reason is None
    assert document is not None
    assert "tax proof for Section 80C" in document.text
    assert "Navigation link" not in document.text


def test_generic_chunking_drops_short_boilerplate_but_keeps_prose():
    settings = load_settings()
    long_body = "The factsheet lists portfolio holdings, sectors, and scheme information. " * 8
    document = {
        "doc_id": "example-educational",
        "source_url": "https://www.mutualfundssahihai.com/en/example",
        "title": "T",
        "page_type": "educational",
        "fetched_at": "2026-11-05T12:00:00Z",
        "text": (
            "# T\n\n"
            "## Short\n\n"
            "Brief note.\n\n"
            "## Long\n\n"
            f"{long_body}\n"
        ),
    }

    chunks = build_chunks(document, settings)

    assert chunks
    assert all(len(chunk["text"]) >= MIN_GENERIC_CHUNK_CHARS for chunk in chunks)
    assert any("factsheet lists portfolio holdings" in chunk["text"] for chunk in chunks)
    assert not any("Brief note." in chunk["text"] for chunk in chunks)
    assert all(chunk["source_url"] == document["source_url"] for chunk in chunks)
