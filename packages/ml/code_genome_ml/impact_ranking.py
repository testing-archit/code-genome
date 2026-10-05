"""Explainable weighted change-impact score and its held-out ranking evaluation.

The weighted score is the spec's first implementation (§6.2):

    impact = 0.35·dependency + 0.30·co-change + 0.20·proximity + 0.15·bug correlation

with every component in [0, 1] and returned per candidate so a reader can see *why* a
file ranks where it does. The weights are fixed, not fitted; ``evaluate_impact_ranking``
measures them on later commits against single-signal baselines, the learned link model,
and a logistic regression that learns the four weights.
"""

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

import networkx as nx
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .impact import (
    MAX_FILES_PER_COMMIT,
    PairContext,
    future_pairs,
    sample_negatives,
)
from .records import ChangeRecord, CommitRecord, ImportRecord, bulk_commit_shas

WEIGHTED_VERSION = "impact-weighted@1"
WEIGHTS: dict[str, float] = {
    "dependency": 0.35,
    "co_change": 0.30,
    "proximity": 0.20,
    "bug_correlation": 0.15,
}
SIGNAL_LABELS: dict[str, str] = {
    "dependency": "dependency strength",
    "co_change": "co-change confidence",
    "proximity": "import-graph proximity",
    "bug_correlation": "shared bug-fix history",
}
SIGNAL_DEFINITIONS: dict[str, str] = {
    "dependency": "1 for a direct import in either direction, 0.5 at two hops, 0.25 at three "
    "(directed import paths), otherwise 0",
    "co_change": "commits that changed both files / commits that changed the target",
    "proximity": "2 / (1 + shortest undirected import distance); beyond 3 hops scores 0 "
    "(the spec's 1/(1+d), rescaled so a direct neighbour scores 1)",
    "bug_correlation": "bug-fix commits that touched both files / bug-fix commits that "
    "touched the target",
}
MAX_HOPS = 3
FIX_PATTERN = re.compile(r"\b(fix(es|ed)?|bug|hotfix|patch|resolve[sd]?|regression)\b", re.I)


def keyword_fix_shas(commits: list[CommitRecord]) -> set[str]:
    """Fallback bug-fix labels when no trained intent model is available."""
    return {commit.sha for commit in commits if FIX_PATTERN.search(commit.message or "")}


def weighted_impact_score(
    signals: Mapping[str, float], weights: Mapping[str, float] = WEIGHTS
) -> float:
    """Weighted sum of the four components, each clipped to [0, 1]."""
    total = sum(
        weight * min(1.0, max(0.0, float(signals.get(name, 0.0))))
        for name, weight in weights.items()
    )
    return round(total, 4)


@dataclass
class WeightedImpact:
    path: str
    score: float
    signals: dict[str, float]


class ImpactSignalContext:
    """History and import-graph statistics for the four weighted-impact components.

    Only changes before ``before`` are used, so one class serves evaluation (history up to
    a cut-off) and serving (``before=None``). Bulk commits should be removed by the caller."""

    def __init__(
        self,
        changes: list[ChangeRecord],
        imports: list[ImportRecord],
        fix_shas: set[str],
        before: datetime | None = None,
    ) -> None:
        edges = {(edge.source, edge.target) for edge in imports if edge.source != edge.target}
        self.directed = nx.DiGraph()
        self.directed.add_edges_from(edges)
        self.reverse = self.directed.reverse(copy=True)
        self.undirected = self.directed.to_undirected()
        by_commit: dict[str, set[str]] = defaultdict(set)
        for change in changes:
            if before is None or change.authored_at < before:
                by_commit[change.commit_sha].add(change.path)
        self.commits: dict[str, int] = defaultdict(int)
        self.pairs: dict[tuple[str, str], int] = defaultdict(int)
        self.partners: dict[str, set[str]] = defaultdict(set)
        self.fixes: dict[str, set[str]] = defaultdict(set)
        for sha, files in by_commit.items():
            if len(files) > MAX_FILES_PER_COMMIT:
                continue
            for path in files:
                self.commits[path] += 1
                if sha in fix_shas:
                    self.fixes[path].add(sha)
            for left, right in combinations(sorted(files), 2):
                self.pairs[(left, right)] += 1
                self.partners[left].add(right)
                self.partners[right].add(left)
        self._hops: dict[str, tuple[dict[str, int], dict[str, int]]] = {}

    @property
    def files(self) -> set[str]:
        return set(self.commits) | set(self.directed)

    def hops(self, path: str) -> tuple[dict[str, int], dict[str, int]]:
        """(directed distance in either direction, undirected distance), up to three hops."""
        if path not in self._hops:
            if path in self.directed:
                directed = dict(
                    nx.single_source_shortest_path_length(self.reverse, path, cutoff=MAX_HOPS)
                )
                forward = nx.single_source_shortest_path_length(
                    self.directed, path, cutoff=MAX_HOPS
                )
                for node, distance in forward.items():
                    directed[node] = min(distance, directed.get(node, distance))
                undirected = dict(
                    nx.single_source_shortest_path_length(self.undirected, path, cutoff=MAX_HOPS)
                )
                self._hops[path] = (directed, undirected)
            else:
                self._hops[path] = ({}, {})
        return self._hops[path]

    def candidates(self, target: str) -> set[str]:
        """Files with any non-zero component for ``target``."""
        _, undirected = self.hops(target)
        return (set(undirected) | self.partners.get(target, set())) - {target}

    def signals(self, target: str, candidate: str) -> dict[str, float]:
        directed, undirected = self.hops(target)
        hops = directed.get(candidate)
        distance = undirected.get(candidate)
        key = (target, candidate) if target < candidate else (candidate, target)
        target_commits = self.commits.get(target, 0)
        target_fixes = self.fixes.get(target, set())
        return {
            "dependency": round(0.5 ** (hops - 1), 4) if hops else 0.0,
            "co_change": (
                round(self.pairs.get(key, 0) / target_commits, 4) if target_commits else 0.0
            ),
            "proximity": round(2 / (1 + distance), 4) if distance else 0.0,
            "bug_correlation": (
                round(len(target_fixes & self.fixes.get(candidate, set())) / len(target_fixes), 4)
                if target_fixes
                else 0.0
            ),
        }


def rank_weighted_impact(
    context: ImpactSignalContext, target: str, limit: int = 20
) -> list[WeightedImpact]:
    """Candidates for ``target`` ordered by the weighted score (ties by path)."""
    scored = []
    for candidate in context.candidates(target):
        signals = context.signals(target, candidate)
        score = weighted_impact_score(signals)
        if score > 0:
            scored.append(WeightedImpact(candidate, score, signals))
    scored.sort(key=lambda item: (-item.score, item.path))
    return scored[:limit]


# ---- Ranking evaluation on held-out commits (spec §10.3, §10.5) -----------------------

RANKING_KS: tuple[int, ...] = (5, 10)
MAX_RANKING_QUERIES = 1500
MIN_RANKING_COMMITS = 30
RANKING_APPROACHES: dict[str, str] = {
    "static_dependency": "Static dependencies only (mean of dependency strength and proximity)",
    "co_change": "Co-change confidence only",
    "weighted": "Weighted score 0.35 / 0.30 / 0.20 / 0.15 (fixed weights)",
    "link_model": "Learned link-prediction model (logistic regression over 10 pair features)",
    "learned_weights": "Logistic regression that learns the four component weights",
}


def ranking_scores(ranked: list[str], relevant: set[str], k: int) -> tuple[float, float, float]:
    """Precision@k, recall@k and average precision@k (normalised by min(|relevant|, k))."""
    hits = [1 if path in relevant else 0 for path in ranked[:k]]
    running, total = 0, 0.0
    for position, hit in enumerate(hits, start=1):
        if hit:
            running += 1
            total += running / position
    return sum(hits) / k, sum(hits) / len(relevant), total / min(len(relevant), k)


def evaluate_impact_ranking(
    changes: list[ChangeRecord],
    imports: list[ImportRecord],
    fix_shas: set[str],
    file_count: int,
) -> dict[str, object]:
    """Rank candidate files for each changed file of a held-out commit.

    Timeline (commit-ordered quantiles): history before t1 trains the learned rankers on
    pairs that co-change in [t1, t2); every approach then ranks with history before t2 and
    is scored on commits after t2. For a query file, the other files of its commit are the
    relevant set. All approaches rank the same candidate pool (import neighbourhood within
    three hops, earlier co-change partners, same directory); zero scores are not ranked."""
    bulk = bulk_commit_shas(changes, file_count)
    changes = [change for change in changes if change.commit_sha not in bulk]
    ordered = sorted({(change.authored_at, change.commit_sha) for change in changes})
    if len(ordered) < MIN_RANKING_COMMITS:
        return {
            "status": "insufficient_data",
            "reason": f"Needs at least {MIN_RANKING_COMMITS} commits with file changes; "
            f"found {len(ordered)}.",
        }
    t1 = ordered[int(len(ordered) * 0.5)][0]
    t2 = ordered[int(len(ordered) * 0.7)][0]
    serving = ImpactSignalContext(changes, imports, fix_shas, t2)
    pair_serving = PairContext(changes, imports, t2)
    known = serving.files

    test_commits: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        if change.authored_at >= t2 and change.path in known:
            test_commits[change.commit_sha].add(change.path)
    usable = [files for _, files in sorted(test_commits.items()) if 1 < len(files) <= 30]
    queries = [(path, files - {path}) for files in usable for path in sorted(files)]
    if len(queries) < 10:
        return {
            "status": "insufficient_data",
            "reason": "Fewer than ten held-out queries: later commits rarely changed several "
            "known files together.",
            "queries": len(queries),
        }
    if len(queries) > MAX_RANKING_QUERIES:
        step = len(queries) / MAX_RANKING_QUERIES
        queries = [queries[int(index * step)] for index in range(MAX_RANKING_QUERIES)]

    # Learned rankers: features from history before t1, labels from co-change in [t1, t2).
    training = PairContext(changes, imports, t1)
    training_signals = ImpactSignalContext(changes, imports, fix_shas, t1)
    train_known = sorted(training.changes)
    positives = future_pairs(changes, set(train_known), t1, t2)
    negatives = sample_negatives(train_known, positives) if len(train_known) >= 2 else set()
    link: tuple[StandardScaler, LogisticRegression] | None = None
    learned: LogisticRegression | None = None
    learned_weights: dict[str, float] | None = None
    y = np.asarray([1] * len(positives) + [0] * len(negatives))
    if len(positives) >= 8 and len(negatives) >= 8:
        pairs = sorted(positives) + sorted(negatives)
        x = np.asarray([training.features(left, right) for left, right in pairs])
        scaler = StandardScaler().fit(x)
        link = (scaler, LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(x), y))
        components = np.asarray(
            [
                [training_signals.signals(left, right)[name] for name in WEIGHTS]
                for left, right in pairs
            ]
        )
        learned = LogisticRegression(C=1.0, max_iter=2000).fit(components, y)
        learned_weights = {
            name: round(float(value), 4)
            for name, value in zip(WEIGHTS, learned.coef_[0], strict=True)
        }

    directories: dict[str, set[str]] = defaultdict(set)
    for path in known:
        directories[path.rsplit("/", 1)[0] if "/" in path else ""].add(path)
    approaches = [
        name
        for name in RANKING_APPROACHES
        if (name != "link_model" or link is not None)
        and (name != "learned_weights" or learned is not None)
    ]
    totals: dict[str, dict[str, list[float]]] = {name: defaultdict(list) for name in approaches}
    weight_vector = np.asarray(list(WEIGHTS.values()))
    pool_sizes = []
    for query, relevant in queries:
        directory = query.rsplit("/", 1)[0] if "/" in query else ""
        pool = sorted((serving.candidates(query) | directories[directory]) - {query})
        pool_sizes.append(len(pool))
        scores: dict[str, np.ndarray] = {}
        if pool:
            components = np.asarray(
                [[serving.signals(query, other)[name] for name in WEIGHTS] for other in pool]
            )
            scores = {
                "static_dependency": (components[:, 0] + components[:, 2]) / 2,
                "co_change": components[:, 1],
                "weighted": components @ weight_vector,
            }
            if link is not None:
                features = np.asarray([pair_serving.features(query, other) for other in pool])
                scores["link_model"] = link[1].predict_proba(link[0].transform(features))[:, 1]
            if learned is not None:
                scores["learned_weights"] = learned.predict_proba(components)[:, 1]
        for name in approaches:
            values = scores.get(name)
            ranked = (
                [
                    pool[index]
                    for index in sorted(range(len(pool)), key=lambda i: (-values[i], pool[i]))
                    if values[index] > 0
                ]
                if values is not None
                else []
            )
            for k in RANKING_KS:
                precision, recall, average = ranking_scores(ranked, relevant, k)
                totals[name][f"precision_at_{k}"].append(precision)
                totals[name][f"recall_at_{k}"].append(recall)
                totals[name][f"map_at_{k}"].append(average)

    results = {
        name: {metric: round(float(np.mean(values)), 4) for metric, values in metrics.items()}
        for name, metrics in totals.items()
    }
    best = max(results, key=lambda name: (results[name]["map_at_10"], name == "weighted"))
    return {
        "status": "evaluated",
        "ks": list(RANKING_KS),
        "queries": len(queries),
        "test_commits": len(usable),
        "periods": {
            "history_before": t1.isoformat(),
            "learned_labels": [t1.isoformat(), t2.isoformat()],
            "test_after": t2.isoformat(),
        },
        "learned_training_pairs": int(len(y)) if link is not None else 0,
        "mean_pool_size": round(float(np.mean(pool_sizes)), 2),
        "approaches": results,
        "descriptions": {name: RANKING_APPROACHES[name] for name in approaches},
        "best_by_map_at_10": best,
        "weights": dict(WEIGHTS),
        "signal_definitions": dict(SIGNAL_DEFINITIONS),
        "learned_component_weights": learned_weights,
        "note": "Relevant files are those changed in the same later commit, so this measures "
        "how well each approach anticipates co-change, not runtime impact. The import graph "
        "is the analysed snapshot's.",
    }
