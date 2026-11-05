"""Phase 4 — split loaded documents into retrieval chunks.

Reads ``data/loaded/{doc_id}.json`` only (no network, no HTML parsing) and
writes ``data/chunks/{doc_id}.jsonl``.

Strategies (from ``implementation.md``):

* ``scheme`` — one chunk per key fact (expense ratio, exit load, minimum SIP,
  lock-in, riskometer, benchmark) plus the factual sections not already covered
  by those facts.
* ``amc`` — split on headings; long sections are windowed with overlap and
  table headers are repeated.
* ``howto`` / ``educational`` / ``factsheet`` — keep a section or procedure
  together, splitting only when the section exceeds the configured size.
* Anything without usable structure falls back to a line-aware character
  window using ``chunking.max_chars`` / ``chunking.overlap_chars``.

Every chunk carries ``chunk_id``, ``text``, ``source_url``, ``title``,
``scheme_or_page_id``, ``section``, and ``fetched_at``. Chunks without a
``source_url`` are dropped. This phase does not embed or index anything.

Usage::

    python -m src.ingest.chunk
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings, load_settings

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
RISK_RE = re.compile(r"\b(Very High|Moderately High|High|Moderate|Low)\s+Risk\b", re.I)
LOCK_IN_RE = re.compile(r"(\d+)\s*(?:Y|Years?|Yr)\s*Lock-in", re.I)
EXIT_LOAD_PREFIX_RE = re.compile(r"^exit load of\s+", re.I)
BENCHMARK_RE = re.compile(r"^fund benchmark\s+(.+)$", re.I)
MANAGER_RE = re.compile(r"view details", re.I)
# JS-rendered educational pages leave short navigation/boilerplate blocks
# ("Skip to main content", "More details are loading", SEBI page markers).
# Real prose chunks on these pages are >= 350 chars, so this removes noise
# without dropping facts. Scheme fact chunks are built separately.
MIN_GENERIC_CHUNK_CHARS = 200

# Facts are emitted in this order so reruns are deterministic.
SCHEME_FACT_ORDER = (
    "Expense Ratio",
    "Exit Load",
    "Minimum SIP",
    "Lock-in",
    "Riskometer",
    "Benchmark",
)

# Scheme-page sections that duplicate the key facts or contain performance
# tooling / glossary copy rather than scheme facts.
SCHEME_SKIP_SECTIONS = {
    "return calculator",
    "minimum investments",
    "understand terms",
    "returns and rankings",
    "compare similar funds",
    "exit load, stamp duty and tax",
}
SCHEME_SKIP_PREFIXES = ("holdings",)

CHUNK_FIELDS = (
    "chunk_id",
    "text",
    "source_url",
    "title",
    "scheme_or_page_id",
    "section",
    "fetched_at",
)


@dataclass(frozen=True)
class Section:
    """One heading and the lines that belong to it."""

    level: int
    heading: str
    body: list[str]
    line_index: int


class ChunkIdGenerator:
    """Stable, collision-free chunk ids within one document."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def next(self, doc_id: str, section: str, kind: str) -> str:
        base = f"{doc_id}-{kind}-{_slug(section)}"
        count = self.counts.get(base, 0) + 1
        self.counts[base] = count
        return base if count == 1 else f"{base}-{count:02d}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "chunk")[:60].strip("-") or "chunk"


def _heading(line: str) -> tuple[int, str] | None:
    match = HEADING_RE.match(line)
    if not match:
        return None
    text = match.group(2).strip()
    if not text:
        return None
    return len(match.group(1)), text


def _clean_body(lines: list[str]) -> list[str]:
    """Trim blank edges and collapse runs of blank lines."""
    cleaned: list[str] = []
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return cleaned


def _parse_sections(
    lines: list[str], *, max_level: int, flatten: bool
) -> list[Section]:
    """Split lines at headings.

    ``flatten=True`` treats every heading as a boundary (AMC pages).
    ``flatten=False`` only splits at headings whose level is ``<= max_level``,
    so deeper headings stay inside their parent section (scheme and how-to
    pages, where a procedure must stay together).
    """
    boundaries: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        parsed = _heading(line)
        if parsed is None:
            continue
        level, heading = parsed
        if flatten or level <= max_level:
            boundaries.append((index, level, heading))

    sections: list[Section] = []
    for position, (index, level, heading) in enumerate(boundaries):
        end = (
            boundaries[position + 1][0]
            if position + 1 < len(boundaries)
            else len(lines)
        )
        sections.append(
            Section(
                level=level,
                heading=heading,
                body=lines[index + 1 : end],
                line_index=index,
            )
        )
    return sections


def _preamble(lines: list[str], sections: list[Section]) -> list[str]:
    if not sections:
        return lines
    return lines[: sections[0].line_index]


def _summary_lines(lines: list[str]) -> list[str]:
    """Lines between the ``<h1>`` title and the next heading."""
    first_heading = next(
        (index for index, line in enumerate(lines) if _heading(line)), None
    )
    if first_heading is None:
        return lines

    next_heading = first_heading + 1
    while next_heading < len(lines) and not _heading(lines[next_heading]):
        next_heading += 1
    return lines[first_heading + 1 : next_heading]


def _label_value(lines: list[str], label: str) -> str | None:
    target = label.strip().lower()
    for index in range(len(lines) - 1):
        if lines[index].strip().lower() == target:
            value = lines[index + 1].strip()
            if value:
                return value
    return None


def _section_body(
    lines: list[str], heading: str, level: int
) -> list[str] | None:
    """Body of an exact heading at an exact level."""
    target = heading.strip().lower()
    for index, line in enumerate(lines):
        parsed = _heading(line)
        if parsed is None:
            continue
        current_level, current_heading = parsed
        if current_level != level or current_heading.lower() != target:
            continue

        body: list[str] = []
        for candidate in lines[index + 1 :]:
            next_heading = _heading(candidate)
            if next_heading is not None and next_heading[0] <= level:
                break
            body.append(candidate)
        return body
    return None


def _extract_scheme_facts(lines: list[str]) -> dict[str, str]:
    """Pull the key scheme facts out of the loaded text."""
    summary = _summary_lines(lines)
    facts: dict[str, str] = {}

    expense = _label_value(summary, "Expense ratio")
    if expense and re.search(r"\d", expense):
        facts["Expense Ratio"] = expense

    min_sip = _label_value(summary, "Min. for SIP")
    if not min_sip:
        investments = _section_body(lines, "Minimum investments", 3) or []
        min_sip = _label_value(investments, "Min. for SIP")
    if min_sip:
        facts["Minimum SIP"] = min_sip

    for line in summary:
        match = RISK_RE.search(line)
        if match:
            facts["Riskometer"] = re.sub(r"\s+", " ", match.group(0)).strip()
            break

    for line in summary:
        match = LOCK_IN_RE.search(line)
        if match:
            facts["Lock-in"] = f"{match.group(1)} years"
            break

    for line in lines:
        match = BENCHMARK_RE.match(line.strip())
        if match:
            facts["Benchmark"] = match.group(1).strip()
            break

    exit_body = _section_body(lines, "Exit load", 4) or []
    exit_load = next((line.strip() for line in exit_body if line.strip()), None)
    if not exit_load:
        history = _section_body(lines, "Exit Load", 3) or []
        exit_load = next(
            (
                line.strip()
                for line in history
                if "exit load" in line.lower() or line.strip().lower() == "nil"
            ),
            None,
        )
    if exit_load:
        exit_load = EXIT_LOAD_PREFIX_RE.sub("", exit_load).strip().rstrip(".").strip()
        if exit_load:
            facts["Exit Load"] = exit_load

    return facts


def _window_lines(
    lines: list[str], max_chars: int, overlap_chars: int
) -> list[list[str]]:
    """Line-aware character windows with optional overlap.

    Long single lines are hard-split so no window exceeds ``max_chars``.
    """
    max_chars = max(1, max_chars)
    overlap_chars = max(0, overlap_chars)

    expanded: list[str] = []
    for line in lines:
        while len(line) > max_chars:
            expanded.append(line[:max_chars])
            line = line[max_chars:]
        if line:
            expanded.append(line)
    lines = expanded
    if not lines:
        return []

    pieces: list[list[str]] = []
    index = 0
    while index < len(lines):
        chunk: list[str] = []
        length = 0
        cursor = index
        while cursor < len(lines):
            addition = len(lines[cursor]) + (1 if chunk else 0)
            if chunk and length + addition > max_chars:
                break
            chunk.append(lines[cursor])
            length += addition
            cursor += 1

        if not chunk:  # defensive; a single line is already <= max_chars
            chunk = [lines[index]]
            cursor = index + 1

        pieces.append(chunk)
        if cursor >= len(lines):
            break

        # Walk backwards from the split point to keep a small overlap.
        next_index = cursor
        overlap_length = 0
        probe = cursor - 1
        while probe > index:
            addition = len(lines[probe]) + (1 if overlap_length else 0)
            if overlap_length + addition > overlap_chars:
                break
            overlap_length += addition
            next_index = probe
            probe -= 1
        index = max(index + 1, next_index)

    return pieces


def _split_body(
    body: list[str], max_chars: int, overlap_chars: int
) -> list[list[str]]:
    """Split a section body, repeating a table header in every window."""
    body = _clean_body(body)
    if not body:
        return []
    if len("\n".join(body)) <= max_chars:
        return [body]

    header: str | None = None
    content = body
    if (
        len(body) > 1
        and "|" in body[0]
        and any("|" in line for line in body[1:])
    ):
        header = body[0]
        content = body[1:]

    effective_max = max(200, max_chars - (len(header) + 1 if header else 0))
    pieces = _window_lines(content, effective_max, overlap_chars)
    if header:
        pieces = [[header, *piece] for piece in pieces]
    return pieces


def _render(title: str, section: str, body: list[str]) -> str:
    parts = [title.strip(), section.strip(), *body]
    return "\n".join(part for part in parts if part).strip()


def _make_chunk(
    document: dict[str, object],
    ids: ChunkIdGenerator,
    *,
    section: str,
    text: str,
    kind: str,
) -> dict[str, str]:
    doc_id = str(document["doc_id"])
    return {
        "chunk_id": ids.next(doc_id, section, kind),
        "text": text,
        "source_url": str(document["source_url"]),
        "title": str(document["title"]),
        "scheme_or_page_id": doc_id,
        "section": section,
        "fetched_at": str(document["fetched_at"]),
    }


def _section_chunks(
    document: dict[str, object],
    ids: ChunkIdGenerator,
    *,
    section: str,
    body: list[str],
    max_chars: int,
    overlap_chars: int,
    min_chars: int = 0,
) -> list[dict[str, str]]:
    title = str(document["title"])
    # Keep the whole rendered chunk (title + section + body) within max_chars.
    prefix_length = len(title) + len(section) + 2
    body_max_chars = max(200, max_chars - prefix_length)
    chunks: list[dict[str, str]] = []
    for piece in _split_body(body, body_max_chars, overlap_chars):
        text = _render(title, section, piece)
        if text and len(text) >= min_chars:
            chunks.append(
                _make_chunk(
                    document, ids, section=section, text=text, kind="section"
                )
            )
    return chunks


def _scheme_chunks(
    document: dict[str, object],
    lines: list[str],
    ids: ChunkIdGenerator,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    title = str(document["title"])

    facts = _extract_scheme_facts(lines)
    for field in SCHEME_FACT_ORDER:
        value = facts.get(field)
        if not value:
            continue
        chunks.append(
            _make_chunk(
                document,
                ids,
                section=field,
                text=f"{title} — {field}: {value}",
                kind="fact",
            )
        )

    sections = _parse_sections(lines, max_level=3, flatten=False)
    position = 0
    while position < len(sections):
        section = sections[position]
        heading_lower = section.heading.lower()

        if section.level == 1:
            position += 1
            continue
        if heading_lower in SCHEME_SKIP_SECTIONS or heading_lower.startswith(
            SCHEME_SKIP_PREFIXES
        ):
            position += 1
            continue

        if heading_lower == "fund management":
            body = list(section.body)
            cursor = position + 1
            while cursor < len(sections) and MANAGER_RE.search(
                sections[cursor].heading
            ):
                body.append(sections[cursor].heading)
                body.extend(sections[cursor].body)
                cursor += 1
            body = _clean_body(body)
            if body:
                chunks.extend(
                    _section_chunks(
                        document,
                        ids,
                        section="Fund management",
                        body=body,
                        max_chars=max_chars,
                        overlap_chars=overlap_chars,
                    )
                )
            position = cursor
            continue

        if MANAGER_RE.search(section.heading):
            # A manager block without the preceding "Fund management" heading.
            body = _clean_body([section.heading, *section.body])
            if body:
                chunks.extend(
                    _section_chunks(
                        document,
                        ids,
                        section="Fund management",
                        body=body,
                        max_chars=max_chars,
                        overlap_chars=overlap_chars,
                    )
                )
            position += 1
            continue

        body = _clean_body(section.body)
        if body:
            chunks.extend(
                _section_chunks(
                    document,
                    ids,
                    section=section.heading,
                    body=body,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
        position += 1

    return chunks


def _amc_chunks(
    document: dict[str, object],
    lines: list[str],
    ids: ChunkIdGenerator,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for section in _parse_sections(lines, max_level=6, flatten=True):
        # The page title section is the AMC overview.
        section_name = "Overview" if section.level == 1 else section.heading
        body = _clean_body(section.body)
        if not body:
            continue
        chunks.extend(
            _section_chunks(
                document,
                ids,
                section=section_name,
                body=body,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    return chunks


def _generic_chunks(
    document: dict[str, object],
    lines: list[str],
    ids: ChunkIdGenerator,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, str]]:
    """How-to / educational / factsheet pages: keep sections together."""
    chunks: list[dict[str, str]] = []
    sections = _parse_sections(lines, max_level=2, flatten=False)

    preamble = _clean_body(_preamble(lines, sections))
    if preamble:
        chunks.extend(
            _section_chunks(
                document,
                ids,
                section="Overview",
                body=preamble,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                min_chars=MIN_GENERIC_CHUNK_CHARS,
            )
        )

    for section in sections:
        section_name = "Overview" if section.level == 1 else section.heading
        body = _clean_body(section.body)
        if not body:
            continue
        chunks.extend(
            _section_chunks(
                document,
                ids,
                section=section_name,
                body=body,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                min_chars=MIN_GENERIC_CHUNK_CHARS,
            )
        )
    return chunks


def build_chunks(
    document: dict[str, object], settings: Settings
) -> list[dict[str, str]]:
    """Build every chunk for one loaded document."""
    text = document.get("text")
    if not isinstance(text, str) or not text.strip():
        return []

    lines = text.splitlines()
    ids = ChunkIdGenerator()
    page_type = str(document.get("page_type") or "").lower()
    max_chars = settings.chunk_max_chars
    overlap_chars = settings.chunk_overlap_chars

    if page_type == "scheme":
        chunks = _scheme_chunks(
            document,
            lines,
            ids,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    elif page_type == "amc":
        chunks = _amc_chunks(
            document,
            lines,
            ids,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    else:
        chunks = _generic_chunks(
            document,
            lines,
            ids,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

    # Fallback for a flat blob with no usable structure.
    if not chunks:
        body = _clean_body(lines)
        if body:
            chunks = _section_chunks(
                document,
                ids,
                section="Document",
                body=body,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )

    return chunks


def _validate_document(
    document: dict[str, object], path: Path
) -> str | None:
    """Return a skip reason, or ``None`` when the document is usable."""
    for field in ("doc_id", "source_url", "title", "fetched_at"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"missing {field}"
    text = document.get("text")
    if not isinstance(text, str) or not text.strip():
        return "missing text"
    if path.stem != document["doc_id"]:
        return f"doc_id {document['doc_id']!r} does not match filename"
    return None


def write_chunks(path: Path, chunks: list[dict[str, str]]) -> None:
    """Write one JSON object per line, dropping chunks without a source URL."""
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            if not chunk.get("source_url") or not chunk.get("text", "").strip():
                continue
            ordered = {field: chunk[field] for field in CHUNK_FIELDS}
            handle.write(json.dumps(ordered, ensure_ascii=False) + "\n")


def chunk_all(
    loaded_dir: Path, chunks_dir: Path, settings: Settings
) -> tuple[int, int, int]:
    """Chunk all documents from loaded_dir into chunks_dir.

    Returns (written_docs, total_chunks, failed_docs).
    """
    if not loaded_dir.is_dir():
        raise FileNotFoundError(f"Loaded directory not found: {loaded_dir}")

    paths = sorted(loaded_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No loaded documents found in {loaded_dir}")

    chunks_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    written = 0
    failed = 0
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("loaded JSON is not an object")

            reason = _validate_document(document, path)
            if reason is not None:
                print(f"  skip {path.name}: {reason}")
                continue

            chunks = build_chunks(document, settings)
            output_path = chunks_dir / f"{document['doc_id']}.jsonl"
            write_chunks(output_path, chunks)

            kept = sum(
                1
                for chunk in chunks
                if chunk.get("source_url") and chunk.get("text", "").strip()
            )
            total += kept
            written += 1
            print(f"  ok   {document['doc_id']}: {kept} chunk(s) -> {output_path.name}")
        except Exception as exc:
            failed += 1
            print(f"  fail {path.name}: {type(exc).__name__}: {exc}")

    return written, total, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chunk loaded documents into data/chunks/."
    )
    parser.parse_args(argv)

    settings = load_settings()
    loaded_dir = settings.project_root / "data" / "loaded"
    chunks_dir = settings.project_root / "data" / "chunks"

    try:
        written, total, failed = chunk_all(loaded_dir, chunks_dir, settings)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(
        f"Done: {written} document(s) written, {total} chunk(s), {failed} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
