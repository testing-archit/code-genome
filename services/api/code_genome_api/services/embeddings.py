"""Optional transformer embeddings for semantic similarity between files.

The ``embeddings`` extra installs sentence-transformers; the model weights are loaded from
the local Hugging Face cache (downloaded once). When either is missing, callers fall back to
LSA and the genome graph says which backend produced its similarity edges. Vectors are cached
per (workspace, snapshot, analysis version), so one tenant's vectors are never served to
another, and a published snapshot's vectors never change.
"""

import importlib.util
import logging
import threading
from collections import OrderedDict
from typing import Any

import numpy as np

from ..config import get_settings

logger = logging.getLogger(__name__)
TRANSFORMER_BACKEND = "sentence-transformer"
_model: Any = None
_model_name: str | None = None
_model_error: str | None = None
_lock = threading.Lock()
_CACHE_SIZE = 16
_vectors: OrderedDict[tuple[str, str, str, str], tuple[list[str], np.ndarray]] = OrderedDict()


def transformer_requested() -> bool:
    backend = get_settings().semantic_backend
    return backend == "transformer" or (
        backend == "auto" and importlib.util.find_spec("sentence_transformers") is not None
    )


def _load() -> Any:
    global _model, _model_name, _model_error
    name = get_settings().embedding_model
    if _model is not None and _model_name == name:
        return _model
    if _model_error is not None and _model_name == name:
        return None
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        _model = SentenceTransformer(name, device="cpu")
        _model_name, _model_error = name, None
    except Exception as error:  # noqa: BLE001 - any load failure means "use LSA"
        _model, _model_name, _model_error = None, name, type(error).__name__
        logger.warning("embedding_model_unavailable", extra={"model": name, "error": _model_error})
    return _model


def model_name() -> str:
    return get_settings().embedding_model


def unit_vectors(
    cache_key: tuple[str, str, str], paths: list[str], texts: list[str]
) -> np.ndarray | None:
    """Normalised embeddings for ``texts`` (one per path), or None when unavailable."""
    key = (*cache_key, model_name())
    with _lock:
        cached = _vectors.get(key)
        if cached is not None and cached[0] == paths:
            _vectors.move_to_end(key)
            return cached[1]
        model = _load()
    if model is None:
        return None
    try:
        vectors = np.asarray(
            model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False),
            dtype=float,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("embedding_encode_failed", extra={"error": type(error).__name__})
        return None
    with _lock:
        _vectors[key] = (list(paths), vectors)
        while len(_vectors) > _CACHE_SIZE:
            _vectors.popitem(last=False)
    return vectors
