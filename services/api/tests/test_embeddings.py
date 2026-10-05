from typing import Any

import numpy as np
import pytest
from code_genome_api.services import embeddings, genome

DOCUMENTS = {
    "src/retry.ts": "retry delay calculate backoff",
    "src/timing.ts": "retry timing backoff compute",
    "src/invoice.ts": "render invoice table",
    "src/table.ts": "table rows render",
    "src/auth.ts": "login session token",
}
KEY = ("ws_one", "snap_one", "structural@1")


def test_lsa_is_used_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "transformer_requested", lambda: False)
    pairs, backend = genome.semantic_pairs_with_backend(DOCUMENTS, KEY)
    assert backend == genome.LSA_BACKEND
    assert pairs == genome.semantic_pairs(DOCUMENTS)


def test_missing_model_falls_back_to_lsa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "transformer_requested", lambda: True)
    monkeypatch.setattr(embeddings, "unit_vectors", lambda *args: None)
    _, backend = genome.semantic_pairs_with_backend(DOCUMENTS, KEY)
    assert backend == genome.LSA_BACKEND


def test_transformer_vectors_produce_labelled_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cache_key: Any, paths: list[str], texts: list[str]) -> np.ndarray:
        # Two clusters: retry/timing and invoice/table; auth stands alone.
        groups = {"retry": 0, "timing": 0, "invoice": 1, "table": 1, "auth": 2}
        vectors = np.zeros((len(paths), 3))
        for row, path in enumerate(paths):
            vectors[row, groups[path.split("/")[1].split(".")[0]]] = 1.0
        return vectors

    monkeypatch.setattr(embeddings, "transformer_requested", lambda: True)
    monkeypatch.setattr(embeddings, "unit_vectors", fake)
    pairs, backend = genome.semantic_pairs_with_backend(DOCUMENTS, KEY)
    assert backend.startswith("sentence-transformer:")
    assert {(left, right) for left, right, _ in pairs} == {
        ("src/retry.ts", "src/timing.ts"),
        ("src/invoice.ts", "src/table.ts"),
    }


def test_vector_cache_is_scoped_to_workspace_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeModel:
        def encode(self, texts: list[str], **_: Any) -> np.ndarray:
            calls.append(len(texts))
            return np.eye(len(texts))

    monkeypatch.setattr(embeddings, "_load", lambda: FakeModel())
    embeddings._vectors.clear()
    paths = sorted(DOCUMENTS)
    texts = [DOCUMENTS[path] for path in paths]
    embeddings.unit_vectors(KEY, paths, texts)
    embeddings.unit_vectors(KEY, paths, texts)
    assert len(calls) == 1
    embeddings.unit_vectors(("ws_two", "snap_one", "structural@1"), paths, texts)
    assert len(calls) == 2
    embeddings._vectors.clear()
