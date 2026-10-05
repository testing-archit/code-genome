"""Cross-project evaluation of defect-proneness prediction (``defect-cross-project@1``).

A single repository's history is small, so its own model is noisy and a simple heuristic can
win. This evaluation asks whether history from *other* repositories helps.

For each repository we build exactly the defect model's dataset (same features, same fix-touch
labels, same 50%/75% temporal split; code metrics are left out because repositories do not all
record them). Then, leaving one repository out at a time:

* cross-project: candidates trained on every other repository's labelled periods, scored on the
  held-out repository's test period;
* within-project: the same candidates trained on the held-out repository's own training period;
* heuristic: the frequency + churn baseline.

Features are standardised per repository using that repository's training-period statistics only,
the usual way to pool projects of different sizes without leaking test data.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from .defect import (
    CANDIDATES,
    MIN_CLASS,
    MIN_COMMITS,
    _build,
    _centrality,
    _features,
    _graph_degrees,
    _heuristic,
    _History,
    _labels,
)
from .records import ChangeRecord, CommitRecord, FileRecord, ImportRecord, bulk_commit_shas

CROSS_VERSION = "defect-cross-project@1"


@dataclass(frozen=True)
class RepoDataset:
    name: str
    x_train: np.ndarray
    y_train: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray


def repository_dataset(
    name: str,
    commits: list[CommitRecord],
    changes: list[ChangeRecord],
    fix_shas: set[str],
    files: list[FileRecord],
    imports: list[ImportRecord],
) -> RepoDataset | str:
    """The defect model's temporal dataset for one repository, or the reason it has none."""
    bulk = bulk_commit_shas(changes, len(files))
    changes = [change for change in changes if change.commit_sha not in bulk]
    changed_shas = {change.commit_sha for change in changes}
    timeline = [
        commit
        for commit in sorted(commits, key=lambda item: item.authored_at)
        if commit.sha in changed_shas
    ]
    if len(timeline) < MIN_COMMITS:
        return f"needs {MIN_COMMITS} commits with file changes; has {len(timeline)}"
    sizes = {item.path: item.size for item in files}
    changed_paths = {change.path for change in changes}
    paths = sorted(path for path in sizes if path in changed_paths) or sorted(changed_paths)
    history = _History(commits, changes, fix_shas)
    fan_in, fan_out = _graph_degrees(imports)
    centrality = _centrality(imports)
    t1 = timeline[int(len(timeline) * 0.5)].authored_at
    t2 = timeline[int(len(timeline) * 0.75)].authored_at
    x_train = _features(paths, history, t1, sizes, fan_in, fan_out, centrality, None)
    y_train = _labels(paths, history, t1, t2)
    x_test = _features(paths, history, t2, sizes, fan_in, fan_out, centrality, None)
    y_test = _labels(paths, history, t2, None)
    if min(y_train.sum(), len(y_train) - y_train.sum()) < MIN_CLASS:
        return "too few fix-touched (or untouched) files in the training period"
    if min(y_test.sum(), len(y_test) - y_test.sum()) < 1:
        return "the test period has a single class"
    return RepoDataset(name, x_train, y_train, x_test, y_test)


def _standardiser(reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scale = reference.std(axis=0)
    return reference.mean(axis=0), np.where(scale > 0, scale, 1.0)


def _scores(truth: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "average_precision": round(float(average_precision_score(truth, predicted)), 4),
        "roc_auc": round(float(roc_auc_score(truth, predicted)), 4),
    }


def evaluate_cross_project(datasets: Sequence[RepoDataset]) -> dict[str, object]:
    """Leave-one-repository-out comparison; needs at least three repositories."""
    if len(datasets) < 3:
        return {
            "model_version": CROSS_VERSION,
            "status": "insufficient_data",
            "reason": (
                f"Needs at least 3 repositories with a usable dataset; found {len(datasets)}."
            ),
        }
    standardised: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for item in datasets:
        mean, scale = _standardiser(item.x_train)
        standardised[item.name] = ((item.x_train - mean) / scale, (item.x_test - mean) / scale)
    per_repository: dict[str, dict[str, object]] = {}
    for held in datasets:
        others = [item for item in datasets if item.name != held.name]
        x_pool = np.vstack([block for item in others for block in standardised[item.name]])
        y_pool = np.concatenate([part for item in others for part in (item.y_train, item.y_test)])
        own_train, own_test = standardised[held.name]
        row: dict[str, object] = {
            "files": len(held.y_test),
            "test_positive": int(held.y_test.sum()),
            "heuristic": _scores(held.y_test, _heuristic(held.x_test)),
        }
        for name in CANDIDATES:
            cross = _build(name).fit(x_pool, y_pool).predict_proba(own_test)[:, 1]
            within = _build(name).fit(own_train, held.y_train).predict_proba(own_test)[:, 1]
            row[f"cross_{name}"] = _scores(held.y_test, cross)
            row[f"within_{name}"] = _scores(held.y_test, within)
        per_repository[held.name] = row
    approaches = ["heuristic"] + [
        f"{scope}_{name}" for scope in ("within", "cross") for name in CANDIDATES
    ]
    summary: dict[str, dict[str, float]] = {}
    for approach in approaches:
        values = [row[approach] for row in per_repository.values()]
        summary[approach] = {
            metric: round(float(np.mean([value[metric] for value in values])), 4)  # type: ignore[index]
            for metric in ("average_precision", "roc_auc")
        }
    best = max(approaches, key=lambda approach: summary[approach]["average_precision"])
    wins = dict.fromkeys(approaches, 0)
    for row in per_repository.values():
        scores = {approach: row[approach]["average_precision"] for approach in approaches}  # type: ignore[index]
        wins[max(approaches, key=lambda approach: scores[approach])] += 1
    return {
        "model_version": CROSS_VERSION,
        "status": "evaluated",
        "repositories": [item.name for item in datasets],
        "per_repository": per_repository,
        "mean": summary,
        "best_mean_average_precision": best,
        "wins": wins,
        "note": (
            "Mean over held-out repositories. Labels are fix-touch labels from the keyword fix "
            "rule; small repositories make per-repository scores noisy."
        ),
    }
