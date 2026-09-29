"""File-level defect-proneness prediction.

Labels follow the SZZ idea at file granularity: a file is *defect-prone* in a period if a
commit classified as a bug fix touched it during that period. Features are computed only
from history before the period, so the model never sees the future it is scored on.

Timeline (commit-ordered quantiles):

    |---- history ----|-- train labels --|-- test labels --|
    0                 t1                 t2               end

* Train set: features from [0, t1), labels from [t1, t2)
* Test set:  features from [0, t2), labels from [t2, end]
* Deployment: model refit on train and test sets, applied to features from [0, end]

Champion/challenger: an L2 logistic regression and a gradient-boosted tree ensemble are
both scored on the test period against the transparent heuristic baseline. The champion
is chosen by average precision. Per-file explanations come from the logistic model:
coefficient × standardised feature value, which is an exact additive decomposition of
its log-odds.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .records import ChangeRecord, CommitRecord, FileRecord, ImportRecord, bulk_commit_shas

MODEL_VERSION = "defect-temporal@1"
RANDOM_STATE = 7
MIN_COMMITS = 24
MIN_CLASS = 3

FEATURES: tuple[str, ...] = (
    "log_commits",
    "log_churn",
    "authors",
    "prior_fixes",
    "recent_commits",
    "days_since_change",
    "age_days",
    "cochange_degree",
    "fan_in",
    "fan_out",
    "log_size_kb",
)

FEATURE_LABELS: dict[str, str] = {
    "log_commits": "commits touching it",
    "log_churn": "lines churned",
    "authors": "distinct authors",
    "prior_fixes": "earlier bug-fix commits",
    "recent_commits": "commits in the last 30 days",
    "days_since_change": "days since last change",
    "age_days": "file age",
    "cochange_degree": "files it changes with",
    "fan_in": "files importing it",
    "fan_out": "files it imports",
    "log_size_kb": "file size",
}


@dataclass
class FilePrediction:
    path: str
    probability: float
    band: str
    contributions: list[tuple[str, float]]
    features: dict[str, float]
    evidence_shas: list[str]


@dataclass
class DefectResult:
    model_version: str
    status: str
    reason: str | None
    dataset: dict[str, object] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)
    champion: str | None = None
    importance: list[tuple[str, float]] = field(default_factory=list)
    calibration: dict[str, list[float]] = field(default_factory=dict)
    predictions: list[FilePrediction] = field(default_factory=list)


class _History:
    def __init__(
        self,
        commits: list[CommitRecord],
        changes: list[ChangeRecord],
        fix_shas: set[str],
    ) -> None:
        self.author = {commit.sha: commit.author for commit in commits}
        self.changes = sorted(changes, key=lambda change: change.authored_at)
        self.fix_shas = fix_shas

    def window(self, start: datetime | None, end: datetime | None) -> list[ChangeRecord]:
        return [
            change
            for change in self.changes
            if (start is None or change.authored_at >= start)
            and (end is None or change.authored_at < end)
        ]


def _graph_degrees(imports: list[ImportRecord]) -> tuple[dict[str, int], dict[str, int]]:
    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    for edge in {(item.source, item.target) for item in imports if item.source != item.target}:
        fan_out[edge[0]] += 1
        fan_in[edge[1]] += 1
    return fan_in, fan_out


def _features(
    paths: list[str],
    history: _History,
    before: datetime | None,
    sizes: dict[str, int],
    fan_in: dict[str, int],
    fan_out: dict[str, int],
) -> np.ndarray:
    window = history.window(None, before)
    reference = before or (window[-1].authored_at if window else datetime.now())
    per_file: dict[str, list[ChangeRecord]] = defaultdict(list)
    by_commit: dict[str, set[str]] = defaultdict(set)
    for change in window:
        per_file[change.path].append(change)
        by_commit[change.commit_sha].add(change.path)
    partners: dict[str, set[str]] = defaultdict(set)
    for files in by_commit.values():
        if 1 < len(files) <= 30:
            for path in files:
                partners[path].update(files - {path})
    rows = []
    for path in paths:
        items = per_file.get(path, [])
        shas = {item.commit_sha for item in items}
        last = max((item.authored_at for item in items), default=None)
        first = min((item.authored_at for item in items), default=None)
        rows.append(
            [
                math.log1p(len(shas)),
                math.log1p(sum(item.churn for item in items)),
                len({history.author.get(sha, "") for sha in shas}),
                len(shas & history.fix_shas),
                len(
                    {item.commit_sha for item in items if (reference - item.authored_at).days <= 30}
                ),
                min(365.0, (reference - last).days) if last else 365.0,
                min(3650.0, (reference - first).days) if first else 0.0,
                len(partners.get(path, ())),
                fan_in.get(path, 0),
                fan_out.get(path, 0),
                math.log1p(sizes.get(path, 0) / 1024),
            ]
        )
    return np.asarray(rows, dtype=float)


def _labels(
    paths: list[str], history: _History, start: datetime, end: datetime | None
) -> np.ndarray:
    touched = {
        change.path
        for change in history.window(start, end)
        if change.commit_sha in history.fix_shas
    }
    return np.asarray([1 if path in touched else 0 for path in paths], dtype=int)


def _heuristic(matrix: np.ndarray) -> np.ndarray:
    """The pre-ML baseline: frequency and churn, normalised within the set."""
    commits = matrix[:, FEATURES.index("log_commits")]
    churn = matrix[:, FEATURES.index("log_churn")]
    scores: np.ndarray = 0.55 * commits / max(commits.max(), 1e-9) + 0.45 * churn / max(
        churn.max(), 1e-9
    )
    return scores


def _logistic() -> Pipeline:
    return Pipeline(
        [("scale", StandardScaler()), ("model", LogisticRegression(C=0.5, max_iter=2000))]
    )


def _boosting() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=8,
        min_samples_leaf=5,
        random_state=RANDOM_STATE,
    )


def _score(truth: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    k = max(1, math.ceil(len(truth) * 0.2))
    top = np.argsort(-scores)[:k]
    return {
        "roc_auc": round(float(roc_auc_score(truth, scores)), 4),
        "average_precision": round(float(average_precision_score(truth, scores)), 4),
        "precision_at_top20pct": round(float(truth[top].mean()), 4),
        "recall_at_top20pct": round(float(truth[top].sum() / max(1, truth.sum())), 4),
    }


def train_defect_model(
    commits: list[CommitRecord],
    changes: list[ChangeRecord],
    fix_shas: set[str],
    files: list[FileRecord],
    imports: list[ImportRecord],
) -> DefectResult:
    bulk = bulk_commit_shas(changes, len(files))
    changes = [change for change in changes if change.commit_sha not in bulk]
    ordered = sorted(commits, key=lambda commit: commit.authored_at)
    changed_shas = {change.commit_sha for change in changes}
    timeline = [commit for commit in ordered if commit.sha in changed_shas]
    dataset: dict[str, object] = {
        "commits_with_changes": len(timeline),
        "fix_commits": len(fix_shas & changed_shas),
        "bulk_commits_excluded": len(bulk),
        "files": len(files),
    }
    if len(timeline) < MIN_COMMITS:
        return DefectResult(
            MODEL_VERSION,
            "insufficient_data",
            f"Needs at least {MIN_COMMITS} commits with file changes; found {len(timeline)}.",
            dataset,
        )

    sizes = {item.path: item.size for item in files}
    changed_paths = {change.path for change in changes}
    paths = sorted(path for path in sizes if path in changed_paths) or sorted(changed_paths)
    history = _History(commits, changes, fix_shas)
    fan_in, fan_out = _graph_degrees(imports)
    t1 = timeline[int(len(timeline) * 0.5)].authored_at
    t2 = timeline[int(len(timeline) * 0.75)].authored_at

    x_train = _features(paths, history, t1, sizes, fan_in, fan_out)
    y_train = _labels(paths, history, t1, t2)
    x_test = _features(paths, history, t2, sizes, fan_in, fan_out)
    y_test = _labels(paths, history, t2, None)
    dataset.update(
        {
            "train_period": [timeline[0].authored_at.isoformat(), t1.isoformat(), t2.isoformat()],
            "test_period": [t2.isoformat(), timeline[-1].authored_at.isoformat()],
            "train_positive": int(y_train.sum()),
            "train_negative": int(len(y_train) - y_train.sum()),
            "test_positive": int(y_test.sum()),
            "test_negative": int(len(y_test) - y_test.sum()),
            "features": list(FEATURES),
        }
    )
    if min(y_train.sum(), len(y_train) - y_train.sum()) < MIN_CLASS:
        return DefectResult(
            MODEL_VERSION,
            "insufficient_data",
            "Too few files were touched by bug-fix commits in the training period to learn from.",
            dataset,
        )

    candidates = {"logistic_regression": _logistic(), "gradient_boosting": _boosting()}
    for model in candidates.values():
        model.fit(x_train, y_train)

    metrics: dict[str, object] = {}
    champion = "logistic_regression"
    calibration: dict[str, list[float]] = {}
    importance: list[tuple[str, float]] = []
    evaluable = min(y_test.sum(), len(y_test) - y_test.sum()) >= 1
    if evaluable:
        metrics["heuristic_baseline"] = _score(y_test, _heuristic(x_test))
        for name, model in candidates.items():
            probabilities = model.predict_proba(x_test)[:, 1]
            scores = _score(y_test, probabilities)
            scores["brier"] = round(float(brier_score_loss(y_test, probabilities)), 4)
            metrics[name] = scores
        champion = max(
            candidates,
            key=lambda name: (metrics[name]["average_precision"], name == "logistic_regression"),  # type: ignore[index]
        )
        champion_probabilities = candidates[champion].predict_proba(x_test)[:, 1]
        bins = min(5, max(2, int(y_test.sum())))
        observed, predicted = calibration_curve(
            y_test, champion_probabilities, n_bins=bins, strategy="quantile"
        )
        calibration = {
            "predicted": [round(float(v), 4) for v in predicted],
            "observed": [round(float(v), 4) for v in observed],
        }
        permutation = permutation_importance(
            candidates[champion],
            x_test,
            y_test,
            scoring="average_precision",
            n_repeats=8,
            random_state=RANDOM_STATE,
        )
        importance = sorted(
            (
                (name, round(float(value), 4))
                for name, value in zip(FEATURES, permutation.importances_mean, strict=True)
            ),
            key=lambda item: -item[1],
        )
        metrics["test_base_rate"] = round(float(y_test.mean()), 4)
    else:
        metrics["note"] = "The test period has a single class, so held-out metrics are unavailable."

    # Deployment model: refit the champion on both labelled periods.
    x_all = np.vstack([x_train, x_test])
    y_all = np.concatenate([y_train, y_test])
    final = _logistic() if champion == "logistic_regression" else _boosting()
    final.fit(x_all, y_all)
    explainer = _logistic().fit(x_all, y_all)
    x_now = _features(paths, history, None, sizes, fan_in, fan_out)
    probabilities = final.predict_proba(x_now)[:, 1]
    scaler: StandardScaler = explainer.named_steps["scale"]
    coefficients = explainer.named_steps["model"].coef_[0]
    contributions = scaler.transform(x_now) * coefficients
    high, medium = (
        np.quantile(probabilities, [0.9, 0.6]) if len(probabilities) >= 5 else (0.66, 0.33)
    )

    fixes_by_path: dict[str, list[str]] = defaultdict(list)
    recent_by_path: dict[str, list[str]] = defaultdict(list)
    for change in reversed(history.changes):
        target = fixes_by_path if change.commit_sha in fix_shas else recent_by_path
        if change.commit_sha not in target[change.path]:
            target[change.path].append(change.commit_sha)

    predictions = []
    for index, path in enumerate(paths):
        probability = float(probabilities[index])
        ranked = sorted(
            zip(FEATURES, contributions[index], strict=True), key=lambda item: -abs(item[1])
        )
        predictions.append(
            FilePrediction(
                path=path,
                probability=round(probability, 4),
                band="high"
                if probability >= high
                else "medium"
                if probability >= medium
                else "low",
                contributions=[(name, round(float(value), 4)) for name, value in ranked[:4]],
                features={
                    name: round(float(value), 4)
                    for name, value in zip(FEATURES, x_now[index], strict=True)
                },
                evidence_shas=(fixes_by_path[path][:3] + recent_by_path[path][:3])[:5],
            )
        )
    predictions.sort(key=lambda item: (-item.probability, item.path))
    return DefectResult(
        model_version=MODEL_VERSION,
        status="trained",
        reason=None,
        dataset=dataset,
        metrics=metrics,
        champion=champion,
        importance=importance,
        calibration=calibration,
        predictions=predictions,
    )
