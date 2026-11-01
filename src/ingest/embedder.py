"""Phase 5 — the single model loader for chunk and query embeddings.

``get_embedder()`` is the only place that loads a sentence-transformers model.
Ingestion (``src.ingest.embed``) and retrieval (a later phase) must both call
it so the same model and the same vector normalization are used everywhere.

The model id comes from ``embedding.model`` in ``config.yaml``. Embeddings are
returned as unit-length vectors, which makes cosine similarity and the
configured ``retrieval.min_score`` meaningful.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from src.config import load_settings


class Embedder:
    """Small wrapper around a loaded SentenceTransformer model."""

    def __init__(self, model: object, model_name: str) -> None:
        self._model = model
        self.model_name = model_name

        dimension = model.get_sentence_embedding_dimension()  # type: ignore[attr-defined]
        if not dimension:
            raise ValueError(
                f"Model {model_name!r} did not report an embedding dimension"
            )
        self.dimension = int(dimension)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as unit-length vectors (cosine-ready)."""
        if not texts:
            return []

        vectors = self._model.encode(  # type: ignore[attr-defined]
            list(texts),
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return vectors.tolist()


def get_embedder(model_name: str | None = None) -> Embedder:
    """Load (and cache) the configured embedding model.

    This is the only model loader in the project. Retrieval must import this
    function so query vectors use exactly the same model and normalization as
    the stored chunk vectors.
    """
    resolved_name = model_name or load_settings().embedding_model
    return _load_embedder(resolved_name)


@lru_cache(maxsize=4)
def _load_embedder(model_name: str) -> Embedder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "sentence-transformers is required for embeddings; "
            "install it with `pip install -r requirements.txt`"
        ) from exc

    model = SentenceTransformer(model_name)
    return Embedder(model, model_name)
