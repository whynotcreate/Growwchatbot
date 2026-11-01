"""Phase 2 — scrape public pages into ``data/raw``.

Reads ``sources/urls.csv`` and performs public HTTP GETs only. Every source
produces ``data/raw/{doc_id}.html`` and ``data/raw/{doc_id}.meta.json``.
This module never cleans text or writes ``data/loaded`` (that is Phase 3).

Usage::

    python -m src.ingest.scrape            # fetch missing pages
    python -m src.ingest.scrape --refresh  # refetch every page
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.config import load_settings

USER_AGENT = "NextLeap/0.1 (educational RAG demo; public pages only)"
REQUEST_TIMEOUT_SECONDS = 30
DELAY_RANGE_SECONDS = (1.0, 2.0)
PAGE_TYPES = {"scheme", "amc", "howto", "educational", "factsheet"}
REQUIRED_COLUMNS = ("doc_id", "page_type", "title", "url")
DOC_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Source:
    """One row from ``sources/urls.csv``."""

    doc_id: str
    page_type: str
    title: str
    url: str


@dataclass(frozen=True)
class ScrapeResult:
    """Outcome for one source: ``fetched``, ``skipped``, or ``failed``."""

    doc_id: str
    url: str
    outcome: str
    http_status: int | None = None
    error: str | None = None


def read_sources(path: Path) -> list[Source]:
    """Read and validate the source list."""
    if not path.is_file():
        raise FileNotFoundError(f"Source list not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [name for name in REQUIRED_COLUMNS if name not in fieldnames]
        if missing:
            raise ValueError(f"{path}: missing column(s): {', '.join(missing)}")

        sources: list[Source] = []
        seen: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            values = {
                name: (row.get(name) or "").strip() for name in REQUIRED_COLUMNS
            }
            blank = [name for name in REQUIRED_COLUMNS if not values[name]]
            if blank:
                raise ValueError(
                    f"{path}:{line_number}: empty value(s) for {', '.join(blank)}"
                )
            if not DOC_ID_PATTERN.fullmatch(values["doc_id"]):
                raise ValueError(
                    f"{path}:{line_number}: invalid doc_id {values['doc_id']!r}; "
                    "use lowercase letters, digits, and hyphens"
                )
            if values["doc_id"] in seen:
                raise ValueError(
                    f"{path}:{line_number}: duplicate doc_id {values['doc_id']!r}"
                )
            if values["page_type"] not in PAGE_TYPES:
                raise ValueError(
                    f"{path}:{line_number}: page_type must be one of "
                    f"{', '.join(sorted(PAGE_TYPES))}"
                )
            if not values["url"].startswith(("https://", "http://")):
                raise ValueError(
                    f"{path}:{line_number}: url must start with http(s)://"
                )

            seen.add(values["doc_id"])
            sources.append(Source(**values))

    if not sources:
        raise ValueError(f"{path}: no sources found")
    return sources


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _write_meta(path: Path, meta: dict[str, object]) -> None:
    path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def scrape_source(
    source: Source,
    raw_dir: Path,
    session: requests.Session,
    *,
    refresh: bool,
) -> ScrapeResult:
    """Fetch one source unless its HTML is already cached.

    A failed refresh leaves any previously cached HTML in place; the meta file
    is still updated so Phase 3 can skip the document.
    """
    html_path = raw_dir / f"{source.doc_id}.html"
    meta_path = raw_dir / f"{source.doc_id}.meta.json"

    if html_path.exists() and not refresh:
        if not meta_path.exists():
            print(
                f"  skip {source.doc_id}: HTML exists but meta is missing "
                "(use --refresh)"
            )
        else:
            print(f"  skip {source.doc_id}: already downloaded")
        return ScrapeResult(source.doc_id, source.url, "skipped")

    fetched_at = _utc_now()
    try:
        response = session.get(
            source.url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        error = f"{type(exc).__name__}: {exc}"
        _write_meta(
            meta_path,
            {
                "doc_id": source.doc_id,
                "url": source.url,
                "fetched_at": fetched_at,
                "http_status": None,
                "content_type": None,
                "error": error,
            },
        )
        print(f"  fail {source.doc_id}: {error}")
        return ScrapeResult(source.doc_id, source.url, "failed", None, error)

    content_type = response.headers.get("Content-Type")
    error: str | None = None

    if response.status_code == 200 and response.content:
        html_path.write_bytes(response.content)
    elif response.status_code == 200:
        error = "Empty response body"
    else:
        error = f"HTTP {response.status_code}"

    _write_meta(
        meta_path,
        {
            "doc_id": source.doc_id,
            "url": source.url,
            "fetched_at": fetched_at,
            "http_status": response.status_code,
            "content_type": content_type,
            "error": error,
        },
    )

    if error:
        print(f"  fail {source.doc_id}: {error}")
        return ScrapeResult(
            source.doc_id, source.url, "failed", response.status_code, error
        )

    print(f"  ok   {source.doc_id}: {len(response.content):,} bytes")
    return ScrapeResult(
        source.doc_id, source.url, "fetched", response.status_code
    )


def scrape_all(
    sources: list[Source],
    raw_dir: Path,
    *,
    refresh: bool = False,
    session: requests.Session | None = None,
) -> list[ScrapeResult]:
    """Scrape every source in order, sleeping between network requests."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    owns_session = session is None
    session = session or requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
    )

    results: list[ScrapeResult] = []
    try:
        for index, source in enumerate(sources):
            result = scrape_source(source, raw_dir, session, refresh=refresh)
            results.append(result)
            if (
                index < len(sources) - 1
                and result.outcome != "skipped"
            ):
                time.sleep(random.uniform(*DELAY_RANGE_SECONDS))
    finally:
        if owns_session:
            session.close()

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape public source pages into data/raw/."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="refetch pages even if raw HTML already exists",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    raw_dir = settings.project_root / "data" / "raw"
    sources = read_sources(settings.sources_path)

    print(f"Scraping {len(sources)} source(s) -> {raw_dir}")
    results = scrape_all(sources, raw_dir, refresh=args.refresh)

    fetched = sum(result.outcome == "fetched" for result in results)
    skipped = sum(result.outcome == "skipped" for result in results)
    failed = sum(result.outcome == "failed" for result in results)
    print(f"Done: {fetched} fetched, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
