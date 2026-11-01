"""Phase 6 — persistent Chroma client and collection helpers.

The ingest stage (``src.ingest.index``) and the query stage (retrieval) must use
the same persist directory and collection name, so both go through the helpers
here instead of constructing a client directly.

Vectors are written and read explicitly (``embeddings=`` / ``query_embeddings=``)
using the shared :func:`src.ingest.embedder.get_embedder`. Chroma's own
embedding function is never used, so the stored vectors always come from the
model named in ``config.yaml``.

The collection is created with cosine distance because Phase 5 normalizes
vectors to unit length; ``retrieval.min_score`` is then a cosine similarity.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from chromadb.api.models.Collection import Collection

# Cosine distance matches the unit-length vectors written by Phase 5.
DISTANCE_SPACE = "cosine"


def _chromadb() -> Any:
    """Import chromadb lazily so a missing install has a clear message."""
    try:
        import chromadb
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "chromadb is required for the vector store; "
            "install it with `pip install -r requirements.txt`"
        ) from exc
    return chromadb


def get_client(persist_dir: str | Path) -> Any:
    """Open (and create if needed) the persistent Chroma client."""
    chromadb = _chromadb()
    path = Path(persist_dir)
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def collection_metadata(embedding_model: str) -> dict[str, str]:
    """Metadata stored on the collection itself."""
    return {
        "embedding_model": embedding_model,
        "hnsw:space": DISTANCE_SPACE,
    }


def _stored_model(collection: Any) -> str | None:
    metadata = collection.metadata or {}
    model = metadata.get("embedding_model")
    return str(model) if model else None


def get_or_create_collection(
    client: Any, name: str, embedding_model: str
) -> Any:
    """Open the collection, creating it on first use.

    Refuses to reuse a collection built with a different embedding model,
    because query vectors would then be incomparable with the stored ones.
    """
    collection = client.get_or_create_collection(
        name=name, metadata=collection_metadata(embedding_model)
    )

    stored = _stored_model(collection)
    if stored is None:
        # Legacy/foreign collection: record the model without touching vectors.
        collection.modify(
            metadata={**(collection.metadata or {}), "embedding_model": embedding_model}
        )
    elif stored != embedding_model:
        raise ValueError(
            f"Collection {name!r} was built with embedding model {stored!r}, "
            f"but config says {embedding_model!r}. Re-run with --rebuild."
        )
    return collection


def delete_collection(client: Any, name: str) -> bool:
    """Delete a collection if it exists. Returns whether anything was deleted."""
    try:
        client.delete_collection(name)
        return True
    except Exception as exc:  # chromadb raises NotFoundError, text varies by version
        message = str(exc).lower()
        if "not exist" in message or "not found" in message:
            return False
        raise


def rebuild_collection(client: Any, name: str, embedding_model: str) -> Any:
    """Delete and recreate the collection (``--rebuild``)."""
    delete_collection(client, name)
    return client.create_collection(
        name=name, metadata=collection_metadata(embedding_model)
    )


def open_collection(
    client: Any, name: str, embedding_model: str | None = None
) -> Any:
    """Open an existing collection for reading (used by retrieval).

    If ``embedding_model`` is given, a mismatch is an error rather than a
    silently wrong query.
    """
    collection = client.get_collection(name)
    if embedding_model:
        stored = _stored_model(collection)
        if stored and stored != embedding_model:
            raise ValueError(
                f"Collection {name!r} was built with embedding model {stored!r}, "
                f"but config says {embedding_model!r}. Rebuild the index."
            )
    return collection
