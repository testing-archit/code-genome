"""Change-impact prediction as temporal link prediction.

Question: if file A changes, which files B are likely to change with it? A pair (A, B)
is positive when the two files change in the same commit during the label period.
Features come from the import graph and from co-change history before that period:

* import distance, common neighbours, Jaccard and Adamic-Adar on the undirected import graph
* earlier co-change count and support
* how often each file changes
* directory proximity

Negatives are sampled uniformly from pairs of files that both existed before the cutoff.
Evaluation holds out source files (grouped split), so the test set asks about files the
model never saw as a query. Baselines: earlier co-change count alone and inverse import
distance alone.
"""

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations

import networkx as nx
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from .records import ChangeRecord, ImportRecord, bulk_commit_shas

MODEL_VERSION = "cochange-linkpred-lr@1"
RANDOM_STATE = 7
MAX_FILES_PER_COMMIT = 30
FEATURES: tuple[str, ...] = (
    "import_distance",
    "common_neighbors",
    "jaccard",
    "adamic_adar",
    "log_past_cochanges",
    "past_support",
    "log_changes_min",
    "log_changes_max",
    "same_directory",
    "directory_distance",
)
FEATURE_LABELS: dict[str, str] = {
    "import_distance": "import distance",
    "common_neighbors": "shared import neighbours",
    "jaccard": "import neighbourhood overlap",
    "adamic_adar": "Adamic-Adar similarity",
    "log_past_cochanges": "earlier co-changes",
    "past_support": "co-change support",
    "log_changes_min": "change frequency (less active file)",
    "log_changes_max": "change frequency (more active file)",
    "same_directory": "same directory",
    "directory_distance": "directory distance",
}


@dataclass
class LinkModel:
    model_version: str
    status: str
    reason: str | None
    dataset: dict[str, object] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)
    coefficients: list[float] = field(default_factory=list)
    intercept: float = 0.0
    mean: list[float] = field(default_factory=list)
    scale: list[float] = field(default_factory=list)


class PairContext:
    """Graph and history statistics needed to featurise any pair of files."""

    def __init__(
        self, changes: list[ChangeRecord], imports: list[ImportRecord], before: datetime | None
    ) -> None:
        self.graph = nx.Graph()
        self.graph.add_edges_from(
            (edge.source, edge.target) for edge in imports if edge.source != edge.target
        )
        by_commit: dict[str, set[str]] = defaultdict(set)
        self.changes: dict[str, int] = defaultdict(int)
        for change in changes:
            if before is None or change.authored_at < before:
                by_commit[change.commit_sha].add(change.path)
        self.pairs: dict[tuple[str, str], int] = defaultdict(int)
        for files in by_commit.values():
            for path in files:
                self.changes[path] += 1
            if len(files) <= MAX_FILES_PER_COMMIT:
                for left, right in combinations(sorted(files), 2):
                    self.pairs[(left, right)] += 1
        self._distances: dict[str, dict[str, int]] = {}

    def distances(self, source: str) -> dict[str, int]:
        if source not in self._distances:
            self._distances[source] = (
                dict(nx.single_source_shortest_path_length(self.graph, source, cutoff=5))
                if source in self.graph
                else {}
            )
        return self._distances[source]

    def features(self, left: str, right: str) -> list[float]:
        distance = self.distances(left).get(right, 6)
        if left in self.graph and right in self.graph:
            left_n = set(self.graph[left])
            right_n = set(self.graph[right])
            common = left_n & right_n
            union = left_n | right_n
            adamic = sum(1 / math.log(max(2, self.graph.degree(node))) for node in common)
        else:
            common, union, adamic = set(), set(), 0.0
        key = (left, right) if left < right else (right, left)
        together = self.pairs.get(key, 0)
        left_changes = self.changes.get(left, 0)
        right_changes = self.changes.get(right, 0)
        left_dir = left.split("/")[:-1]
        right_dir = right.split("/")[:-1]
        shared = 0
        for a, b in zip(left_dir, right_dir, strict=False):
            if a != b:
                break
            shared += 1
        return [
            float(distance),
            float(len(common)),
            len(common) / len(union) if union else 0.0,
            adamic,
            math.log1p(together),
            together / max(1, min(left_changes, right_changes)),
            math.log1p(min(left_changes, right_changes)),
            math.log1p(max(left_changes, right_changes)),
            1.0 if left_dir == right_dir else 0.0,
            float(len(left_dir) + len(right_dir) - 2 * shared),
        ]


def sample_negatives(known: list[str], positives: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Up to three uniformly sampled non-co-changing pairs per positive pair."""
    rng = random.Random(RANDOM_STATE)
    negatives: set[tuple[str, str]] = set()
    attempts = 0
    target = min(len(positives) * 3, len(known) * (len(known) - 1) // 2 - len(positives))
    while len(negatives) < target and attempts < target * 20:
        attempts += 1
        left, right = sorted(rng.sample(known, 2))
        if (left, right) not in positives:
            negatives.add((left, right))
    return negatives


def future_pairs(
    changes: list[ChangeRecord], known: set[str], start: datetime, end: datetime | None
) -> set[tuple[str, str]]:
    future: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        in_window = change.authored_at >= start and (end is None or change.authored_at < end)
        if in_window and change.path in known:
            future[change.commit_sha].add(change.path)
    positives: set[tuple[str, str]] = set()
    for files in future.values():
        if len(files) <= MAX_FILES_PER_COMMIT:
            positives.update(combinations(sorted(files), 2))
    return positives


def train_link_model(
    changes: list[ChangeRecord], imports: list[ImportRecord], file_count: int
) -> LinkModel:
    bulk = bulk_commit_shas(changes, file_count)
    changes = [change for change in changes if change.commit_sha not in bulk]
    ordered = sorted({(change.authored_at, change.commit_sha) for change in changes})
    if len(ordered) < 20:
        return LinkModel(
            MODEL_VERSION, "insufficient_data", f"Needs at least 20 commits; found {len(ordered)}."
        )
    cutoff = ordered[int(len(ordered) * 0.7)][0]
    context = PairContext(changes, imports, cutoff)
    known = sorted(context.changes)
    positives = future_pairs(changes, set(known), cutoff, None)
    dataset: dict[str, object] = {
        "cutoff": cutoff.isoformat(),
        "files_before_cutoff": len(known),
        "positive_pairs": len(positives),
        "bulk_commits_excluded": len(bulk),
    }
    if len(positives) < 8 or len(known) < 6:
        return LinkModel(
            MODEL_VERSION,
            "insufficient_data",
            "Too few files changed together after the cutoff to learn from.",
            dataset,
        )

    negatives = sample_negatives(known, positives)
    if len(negatives) < 8:
        return LinkModel(
            MODEL_VERSION,
            "insufficient_data",
            "Almost every file pair changed together, so there is nothing to contrast.",
            dataset,
        )
    pairs = [(pair, 1) for pair in sorted(positives)] + [(pair, 0) for pair in sorted(negatives)]
    x = np.asarray([context.features(left, right) for (left, right), _ in pairs])
    y = np.asarray([label for _, label in pairs])
    groups = np.asarray([left for (left, _), _ in pairs])
    dataset.update({"negative_pairs": len(negatives), "features": list(FEATURES)})

    metrics: dict[str, object] = {}
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=RANDOM_STATE)
    train_index, test_index = next(splitter.split(x, y, groups))
    if len(set(y[train_index])) == 2 and len(set(y[test_index])) == 2:
        scaler = StandardScaler().fit(x[train_index])
        model = LogisticRegression(C=1.0, max_iter=2000).fit(
            scaler.transform(x[train_index]), y[train_index]
        )
        probabilities = model.predict_proba(scaler.transform(x[test_index]))[:, 1]
        truth = y[test_index]
        metrics = {
            "model": {
                "roc_auc": round(float(roc_auc_score(truth, probabilities)), 4),
                "average_precision": round(float(average_precision_score(truth, probabilities)), 4),
            },
            "baseline_past_cochange": {
                "roc_auc": round(
                    float(
                        roc_auc_score(truth, x[test_index, FEATURES.index("log_past_cochanges")])
                    ),
                    4,
                ),
            },
            "baseline_import_distance": {
                "roc_auc": round(
                    float(roc_auc_score(truth, -x[test_index, FEATURES.index("import_distance")])),
                    4,
                ),
            },
            "test_pairs": len(test_index),
            "test_base_rate": round(float(truth.mean()), 4),
        }
    else:
        metrics["note"] = (
            "The held-out split had a single class, so held-out metrics are unavailable."
        )

    scaler = StandardScaler().fit(x)
    model = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(x), y)
    return LinkModel(
        model_version=MODEL_VERSION,
        status="trained",
        reason=None,
        dataset=dataset,
        metrics=metrics,
        coefficients=[round(float(value), 6) for value in model.coef_[0]],
        intercept=round(float(model.intercept_[0]), 6),
        mean=[round(float(value), 6) for value in scaler.mean_],
        scale=[round(float(value), 6) if value else 1.0 for value in scaler.scale_],
    )


@dataclass
class ImpactPrediction:
    path: str
    probability: float
    reasons: list[str]


def predict_impact(
    params: dict[str, object],
    path: str,
    changes: list[ChangeRecord],
    imports: list[ImportRecord],
    candidates: list[str],
    limit: int = 15,
) -> list[ImpactPrediction]:
    """Score candidate partners for ``path`` with a stored model (features from full history)."""
    bulk = bulk_commit_shas(changes, len(candidates))
    changes = [change for change in changes if change.commit_sha not in bulk]
    coefficients = np.asarray(params["coefficients"], dtype=float)
    mean = np.asarray(params["mean"], dtype=float)
    scale = np.asarray(params["scale"], dtype=float)
    intercept = float(params["intercept"])  # type: ignore[arg-type]
    context = PairContext(changes, imports, None)
    nearby = set(context.distances(path)) - {path}
    partners = {
        right if left == path else left for left, right in context.pairs if path in (left, right)
    }
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    same_dir = {
        item
        for item in candidates
        if item != path and (item.rsplit("/", 1)[0] if "/" in item else "") == directory
    }
    pool = sorted((nearby | partners | same_dir) & set(candidates))
    if not pool:
        return []
    matrix = np.asarray([context.features(path, other) for other in pool])
    standardized = (matrix - mean) / scale
    contributions = standardized * coefficients
    logits = contributions.sum(axis=1) + intercept
    probabilities = 1 / (1 + np.exp(-logits))
    order = np.argsort(-probabilities)[:limit]
    results = []
    for index in order:
        top = [
            FEATURE_LABELS[FEATURES[i]]
            for i in np.argsort(-contributions[index])[:2]
            if contributions[index][i] > 0
        ]
        results.append(ImpactPrediction(pool[index], round(float(probabilities[index]), 4), top))
    return results
