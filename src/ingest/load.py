"""Phase 3 — load raw HTML into cleaned documents.

Reads ``data/raw/{doc_id}.html`` and ``data/raw/{doc_id}.meta.json`` (no
network) and writes ``data/loaded/{doc_id}.json`` only when the fetch status
is 200 and the extracted text is non-empty. Chunking (Phase 4) reads only
this folder.

``page_type`` is taken from the canonical ``sources/urls.csv`` because it is
not stored in the raw meta file.

Usage::

    python -m src.ingest.load
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from src.config import load_settings
from src.ingest.scrape import Source, read_sources

# Tags that never contribute visible page text.
SKIP_TAGS = {
    "canvas",
    "footer",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}

HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}

# Remove blocks that are almost entirely links (site headers, footers,
# sidebars). This catches navigation that is not wrapped in <nav>.
MIN_LINK_BLOCK_CHARS = 200
LINK_DENSITY_THRESHOLD = 0.85
# A wrapper can have high link density because it contains a footer while still
# holding a short FAQ answer. Keep it when this much non-link prose survives.
MIN_PROSE_CHARS = 80


@dataclass(frozen=True)
class LoadedDocument:
    """The Phase 3 output contract for one document."""

    doc_id: str
    source_url: str
    title: str
    page_type: str
    fetched_at: str
    http_status: int
    text: str


@dataclass(frozen=True)
class LoadResult:
    """Outcome for one source: ``loaded``, ``skipped``, or ``failed``."""

    doc_id: str
    outcome: str
    reason: str | None = None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _link_density(element: Tag) -> float:
    total = len(_normalize(element.get_text(" ", strip=True)))
    if not total:
        return 0.0
    linked = sum(
        len(_normalize(link.get_text(" ", strip=True)))
        for link in element.find_all("a")
    )
    return linked / total


def _prune_link_heavy(
    element: Tag,
    *,
    min_chars: int = MIN_LINK_BLOCK_CHARS,
    threshold: float = LINK_DENSITY_THRESHOLD,
) -> None:
    """Drop link-only blocks while keeping the surrounding content.

    The check uses non-link prose, not raw link density, so a page wrapper
    holding a short answer plus a large footer is not deleted wholesale.
    """
    for child in list(element.children):
        if not isinstance(child, Tag):
            continue
        if child.name in SKIP_TAGS:
            child.decompose()
            continue
        text = _normalize(child.get_text(" ", strip=True))
        if len(text) >= min_chars and _link_density(child) >= threshold:
            linked = sum(
                len(_normalize(link.get_text(" ", strip=True)))
                for link in child.find_all("a")
            )
            if len(text) - linked < MIN_PROSE_CHARS:
                child.decompose()
                continue
        _prune_link_heavy(child, min_chars=min_chars, threshold=threshold)


def _render_node(element: Tag) -> list[str]:
    """Render visible text as newline-separated lines.

    Headings keep a Markdown-style prefix (``#`` … ``######``), table rows are
    joined with `` | ``, and list items get a ``- `` prefix so Phase 4 can
    chunk on structure instead of guessing from a flat blob.
    """
    if element.name in SKIP_TAGS:
        return []
    if element.name == "br":
        return [""]
    if element.name in HEADING_LEVELS:
        text = _normalize(element.get_text(" ", strip=True))
        if not text:
            return []
        return [f"{'#' * HEADING_LEVELS[element.name]} {text}"]
    if element.name == "tr":
        cells = [
            _normalize(cell.get_text(" ", strip=True))
            for cell in element.find_all(["th", "td"], recursive=False)
        ]
        cells = [cell for cell in cells if cell]
        return [" | ".join(cells)] if cells else []
    if element.name == "li":
        has_block_children = any(
            isinstance(child, Tag) and child.name in BLOCK_TAGS
            for child in element.children
        )
        if not has_block_children:
            text = _normalize(element.get_text(" ", strip=True))
            return [f"- {text}"] if text else []

    block_children = [
        child
        for child in element.children
        if isinstance(child, Tag) and child.name in BLOCK_TAGS
    ]
    if not block_children:
        text = _normalize(element.get_text(" ", strip=True))
        return [text] if text else []

    lines: list[str] = []
    for child in element.children:
        if isinstance(child, NavigableString):
            text = _normalize(str(child))
            if text:
                lines.append(text)
        elif isinstance(child, Tag):
            lines.extend(_render_node(child))
    return lines


def _extract_text(soup: BeautifulSoup) -> str:
    root = soup.body or soup
    lines = [line for line in _render_node(root) if line]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_title(soup: BeautifulSoup, source: Source) -> str:
    heading = soup.find("h1")
    if heading is not None:
        text = _normalize(heading.get_text(" ", strip=True))
        if text:
            return text
    if source.title:
        return source.title
    if soup.title is not None:
        text = _normalize(soup.title.get_text(" ", strip=True))
        if text:
            return text
    return source.doc_id


def load_document(
    source: Source, raw_dir: Path
) -> tuple[LoadedDocument | None, str | None]:
    """Load one raw document, or return ``(None, reason)`` if it is skipped."""
    meta_path = raw_dir / f"{source.doc_id}.meta.json"
    html_path = raw_dir / f"{source.doc_id}.html"

    if not meta_path.is_file():
        return None, "missing meta file"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable meta: {exc}"

    if meta.get("doc_id") not in (None, source.doc_id):
        return None, f"meta doc_id mismatch: {meta.get('doc_id')!r}"
    if meta.get("url") not in (None, source.url):
        return None, f"meta URL mismatch: {meta.get('url')!r}"

    status = meta.get("http_status")
    if status != 200:
        return None, f"http_status={status!r}"

    fetched_at = meta.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at:
        return None, "missing fetched_at"

    if not html_path.is_file():
        return None, "missing HTML file"
    try:
        html = html_path.read_bytes()
    except OSError as exc:
        return None, f"unreadable HTML: {exc}"

    soup = BeautifulSoup(html, "html.parser")
    title = _extract_title(soup, source)

    for tag in soup.find_all(list(SKIP_TAGS)):
        tag.decompose()
    _prune_link_heavy(soup)
    text = _extract_text(soup)

    if not text:
        return None, "empty text after cleaning"

    return (
        LoadedDocument(
            doc_id=source.doc_id,
            source_url=source.url,
            title=title,
            page_type=source.page_type,
            fetched_at=fetched_at,
            http_status=status,
            text=text,
        ),
        None,
    )


def load_all(
    sources: list[Source], raw_dir: Path, loaded_dir: Path
) -> list[LoadResult]:
    """Load every source, writing cleaned JSON for successful documents."""
    loaded_dir.mkdir(parents=True, exist_ok=True)

    results: list[LoadResult] = []
    for source in sources:
        try:
            document, reason = load_document(source, raw_dir)
            if document is None:
                results.append(LoadResult(source.doc_id, "skipped", reason))
                print(f"  skip {source.doc_id}: {reason}")
                continue

            output_path = loaded_dir / f"{source.doc_id}.json"
            output_path.write_text(
                json.dumps(asdict(document), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            results.append(LoadResult(source.doc_id, "loaded"))
            print(f"  ok   {source.doc_id}: {len(document.text):,} chars")
        except Exception as exc:  # keep one bad document from stopping the rest
            reason = f"{type(exc).__name__}: {exc}"
            results.append(LoadResult(source.doc_id, "failed", reason))
            print(f"  fail {source.doc_id}: {reason}")

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load raw HTML into cleaned documents in data/loaded/."
    )
    parser.parse_args(argv)

    settings = load_settings()
    raw_dir = settings.project_root / "data" / "raw"
    loaded_dir = settings.project_root / "data" / "loaded"
    sources = read_sources(settings.sources_path)

    print(f"Loading {len(sources)} document(s) from {raw_dir} -> {loaded_dir}")
    results = load_all(sources, raw_dir, loaded_dir)

    loaded = sum(result.outcome == "loaded" for result in results)
    skipped = sum(result.outcome == "skipped" for result in results)
    failed = sum(result.outcome == "failed" for result in results)
    print(f"Done: {loaded} loaded, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
