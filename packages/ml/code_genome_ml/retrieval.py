"""Hybrid repository retrieval: BM25 + latent semantic embeddings + rank fusion.

* **BM25** (Okapi, k1=1.5, b=0.75) over identifier-aware tokens catches exact terms.
* **LSA embeddings**: TF-IDF followed by truncated SVD gives dense document vectors in
  which terms that co-occur (``invoice``, ``billing``, ``payment``) sit close together,
  so a query can match a file that never uses its exact words.
* **Reciprocal rank fusion** (k=60) merges the two rankings without score calibration.

The ranking mode used in production is chosen per repository from the evaluation below
(``select_mode``), so a repository where plain BM25 retrieves better keeps BM25.

``evaluate_retrieval`` measures this without human labels: each commit message is used
as a query and the files that commit changed are the relevant results. Commit text is
never indexed during evaluation, so there is no leakage.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .records import SearchDocument
from .text import tokenize

MODEL_VERSION = "hybrid-bm25-lsa-rrf@1"
RRF_K = 60


@dataclass
class SearchHit:
    document: SearchDocument
    score: float
    bm25: float
    semantic: float
    bm25_rank: int | None
    semantic_rank: int | None


class HybridRetriever:
    def __init__(self, documents: list[SearchDocument], *, dimensions: int = 96) -> None:
        self.documents = documents
        self.tokens = [tokenize(document.text) for document in documents]
        self.doc_freq: Counter[str] = Counter()
        for tokens in self.tokens:
            self.doc_freq.update(set(tokens))
        self.lengths = np.asarray([len(tokens) for tokens in self.tokens], dtype=float)
        self.avg_length = float(self.lengths.mean()) if len(self.lengths) else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for index, tokens in enumerate(self.tokens):
            for term, tf in Counter(tokens).items():
                self.postings.setdefault(term, []).append((index, tf))
        self.vectorizer: TfidfVectorizer | None = None
        self.svd: TruncatedSVD | None = None
        self.embeddings: np.ndarray | None = None
        if len(documents) >= 3:
            vectorizer = TfidfVectorizer(analyzer=tokenize, sublinear_tf=True, min_df=1)
            matrix = vectorizer.fit_transform([document.text for document in documents])
            rank = min(dimensions, matrix.shape[0] - 1, matrix.shape[1] - 1)
            self.vectorizer = vectorizer
            if rank >= 2:
                self.svd = TruncatedSVD(n_components=rank, random_state=7)
                self.embeddings = normalize(self.svd.fit_transform(matrix))

    @property
    def dimensions(self) -> int:
        return int(self.svd.n_components) if self.svd is not None else 0

    def bm25(self, query: list[str]) -> np.ndarray:
        scores = np.zeros(len(self.documents))
        total = len(self.documents)
        for term in set(query):
            df = self.doc_freq.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            for index, tf in self.postings[term]:
                norm = 1.5 * (0.25 + 0.75 * self.lengths[index] / max(self.avg_length, 1e-9))
                scores[index] += idf * tf * 2.5 / (tf + norm)
        return scores

    def semantic(self, query: str) -> np.ndarray:
        if self.vectorizer is None or self.svd is None or self.embeddings is None:
            return np.zeros(len(self.documents))
        vector = self.svd.transform(self.vectorizer.transform([query]))
        if not np.any(vector):
            return np.zeros(len(self.documents))
        similarity: np.ndarray = self.embeddings @ normalize(vector)[0]
        return similarity

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        kinds: set[str] | None = None,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        if not self.documents:
            return []
        terms = tokenize(query)
        lexical = self.bm25(terms)
        dense = self.semantic(query)
        allowed = np.asarray(
            [kinds is None or document.kind in kinds for document in self.documents]
        )
        lexical_order = [i for i in np.argsort(-lexical) if lexical[i] > 0 and allowed[i]]
        dense_order = [i for i in np.argsort(-dense) if dense[i] > 0.05 and allowed[i]]
        lexical_rank = {index: rank for rank, index in enumerate(lexical_order, start=1)}
        dense_rank = {index: rank for rank, index in enumerate(dense_order, start=1)}
        if mode == "bm25":
            fused = {index: lexical[index] for index in lexical_order}
        elif mode == "semantic":
            fused = {index: dense[index] for index in dense_order}
        else:
            fused = {}
            for index, rank in lexical_rank.items():
                fused[index] = fused.get(index, 0.0) + 1 / (RRF_K + rank)
            for index, rank in dense_rank.items():
                fused[index] = fused.get(index, 0.0) + 1 / (RRF_K + rank)
        ordered = sorted(fused, key=lambda index: (-fused[index], self.documents[index].id))[:limit]
        return [
            SearchHit(
                document=self.documents[index],
                score=round(float(fused[index]), 6),
                bm25=round(float(lexical[index]), 4),
                semantic=round(float(dense[index]), 4),
                bm25_rank=lexical_rank.get(index),
                semantic_rank=dense_rank.get(index),
            )
            for index in ordered
        ]


def evaluate_retrieval(
    file_documents: list[SearchDocument],
    queries: list[tuple[str, set[str]]],
    *,
    k: int = 10,
) -> dict[str, object]:
    """MRR@k and recall@k for BM25, semantic, and hybrid ranking over file documents."""
    usable = [
        (text, relevant) for text, relevant in queries if relevant and len(tokenize(text)) >= 2
    ][:300]
    if len(file_documents) < 5 or len(usable) < 5:
        return {"status": "insufficient_data", "queries": len(usable)}
    retriever = HybridRetriever(file_documents)
    results: dict[str, object] = {
        "status": "evaluated",
        "queries": len(usable),
        "documents": len(file_documents),
        "k": k,
    }
    for mode in ("bm25", "semantic", "hybrid"):
        reciprocal = []
        recall = []
        for text, relevant in usable:
            hits = [hit.document.path for hit in retriever.search(text, limit=k, mode=mode)]
            rank = next(
                (position for position, path in enumerate(hits, start=1) if path in relevant), None
            )
            reciprocal.append(1 / rank if rank else 0.0)
            recall.append(len(relevant & set(hits)) / len(relevant))
        results[mode] = {
            "mrr": round(float(np.mean(reciprocal)), 4),
            f"recall_at_{k}": round(float(np.mean(recall)), 4),
        }
    results["random_baseline_recall"] = round(min(1.0, k / len(file_documents)), 4)
    results["selected_mode"] = select_mode(results)
    return results


def select_mode(results: Mapping[str, object]) -> str:
    """Champion ranking mode for this repository: best recall@10, then MRR.

    Hybrid is kept unless another mode beats it by more than 0.01, because fusion is
    more robust to queries unlike commit messages (for example Hinglish questions)."""

    def key(mode: str) -> tuple[float, float]:
        scores = results.get(mode)
        if not isinstance(scores, dict):
            return (0.0, 0.0)
        return (float(scores.get("recall_at_10", 0.0)), float(scores.get("mrr", 0.0)))

    best = max(("hybrid", "bm25", "semantic"), key=key)
    return "hybrid" if key(best)[0] - key("hybrid")[0] <= 0.01 else best
