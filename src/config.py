"""Application configuration.

Loads committed defaults from ``config.yaml`` and secrets/model overrides from
``.env``. Every pipeline stage should import :func:`load_settings` so the
embedding model, Chroma location, and retrieval settings stay in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class Settings:
    """Resolved settings for a single run of the pipeline."""

    project_root: Path
    sources_path: Path
    embedding_model: str
    chunking_strategy: str
    chunk_max_chars: int
    chunk_overlap_chars: int
    chroma_persist_dir: Path
    chroma_collection: str
    retrieval_top_k: int
    retrieval_min_score: float
    llm_provider: str
    llm_model: str | None
    groq_api_key: str | None
    prompt_system_id: str = "v1"


def _resolve_path(value: str | Path, root: Path) -> Path:
    """Resolve a config path against the project root, not the current cwd."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _require(section: dict[str, Any], key: str, where: str) -> Any:
    if key not in section:
        raise ValueError(f"Missing required config key: {where}.{key}")
    return section[key]


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load ``config.yaml`` and ``.env`` and return resolved settings.

    Relative paths in ``config.yaml`` are resolved against the project root.
    Variables already present in the process environment win over ``.env``.
    """
    config_file = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not config_file.is_file():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with config_file.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    root = config_file.resolve().parent
    load_dotenv(root / ".env", override=False)

    sources = config.get("sources") or {}
    embedding = config.get("embedding") or {}
    chunking = config.get("chunking") or {}
    chroma = config.get("chroma") or {}
    retrieval = config.get("retrieval") or {}
    llm = config.get("llm") or {}
    prompts = config.get("prompts") or {}

    return Settings(
        project_root=root,
        sources_path=_resolve_path(_require(sources, "path", "sources"), root),
        embedding_model=_require(embedding, "model", "embedding"),
        chunking_strategy=chunking.get("strategy", "structure_aware"),
        chunk_max_chars=int(chunking.get("max_chars", 1200)),
        chunk_overlap_chars=int(chunking.get("overlap_chars", 200)),
        chroma_persist_dir=_resolve_path(
            _require(chroma, "persist_dir", "chroma"), root
        ),
        chroma_collection=_require(chroma, "collection", "chroma"),
        retrieval_top_k=int(_require(retrieval, "top_k", "retrieval")),
        retrieval_min_score=float(_require(retrieval, "min_score", "retrieval")),
        llm_provider=llm.get("provider", "groq"),
        llm_model=(os.getenv("LLM_MODEL") or "").strip() or None,
        groq_api_key=(os.getenv("GROQ_API_KEY") or "").strip() or None,
        prompt_system_id=str(prompts.get("system_id", "v1")),
    )


if __name__ == "__main__":
    print(load_settings())
