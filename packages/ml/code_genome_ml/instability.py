"""Component instability forecasting from recent activity sequences.

A *component* is a directory-level group of the snapshot's files (``group_components``).
History is cut into fixed periods: calendar weeks when the repository commits often
enough, otherwise equal commit-count windows. For every component and period we record
non-fix commits, bug-fix commits (commits the intent model classified as ``fix``), log
churn, and distinct authors.

Label: the component is touched by at least one bug-fix commit in the *next* period.

Model: an L2 logistic regression over the last ``WINDOW`` periods of those features
(lagged, standardised), plus the component's fix rate so far and its size. This is a
windowed (autoregressive) sequence model. It is not a recurrent network: there is no
hidden state carried across periods beyond the fixed window.

Evaluation is temporal. Samples are ordered by the period they predict; the latest 25% of
target periods form the hold-out, and the model never sees features or labels from them
during fitting. It is compared with two baselines on the same hold-out:

* persistence: next period repeats the current one (fix now → fix next);
* historical rate: the component's share of earlier periods with a fix.

The decision threshold for precision/recall/F1 is chosen on training data only.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .components import group_components
from .records import ChangeRecord, CommitRecord, FileRecord, bulk_commit_shas

MODEL_VERSION = "instability-windowed-logreg@1"
WINDOW = 4
MIN_COMMITS = 30
MIN_PERIODS = WINDOW + 6
MIN_CLASS = 3
MIN_WEEKLY_RATE = 3.0
RANDOM_STATE = 7

PERIOD_FEATURES: tuple[str, ...] = ("other_commits", "fix_commits", "log_churn", "authors")
PERIOD_LABELS: dict[str, str] = {
    "other_commits": "non-fix commits",
    "fix_commits": "bug-fix commits",
    "log_churn": "lines churned (log)",
    "authors": "distinct authors",
}
STATIC_FEATURES: tuple[str, ...] = ("fix_rate_so_far", "log_files")
FEATURES: tuple[str, ...] = (
    *(f"{name}@t-{lag}" for lag in range(WINDOW) for name in PERIOD_FEATURES),
    *STATIC_FEATURES,
)


def _label(feature: str) -> str:
    if feature == "fix_rate_so_far":
        return "share of earlier periods with a fix"
    if feature == "log_files":
        return "component size (files, log)"
    name, lag = feature.split("@t-")
    when = "this period" if lag == "0" else f"{lag} period{'s' if lag != '1' else ''} ago"
    return f"{PERIOD_LABELS[name]}, {when}"


FEATURE_LABELS: dict[str, str] = {name: _label(name) for name in FEATURES}


@dataclass
class ComponentForecast:
    component: str
    probability: float
    band: str
    files: int
    recent_periods: list[list[int]]  # [non-fix commits, fix commits], oldest → newest
    fixed_last_period: bool
    contributions: list[tuple[str, float]]
    evidence_shas: list[str]


@dataclass
class InstabilityResult:
    model_version: str
    status: str
    reason: str | None
    dataset: dict[str, object] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)
    coefficients: list[tuple[str, float]] = field(default_factory=list)
    predictions: list[ComponentForecast] = field(default_factory=list)
    feature_labels: dict[str, str] = field(default_factory=lambda: dict(FEATURE_LABELS))
    contribution_method: str = (
        "Logistic-regression coefficient × standardised feature value (log-odds); "
        "an exact additive decomposition of the model's score."
    )


def _assign_periods(
    timeline: list[CommitRecord],
) -> tuple[dict[str, int], list[datetime], dict[str, object]]:
    """Map commit SHA → period index and return each period's start.

    Calendar weeks when the history is dense enough, otherwise equal commit-count windows."""
    first, last = timeline[0].authored_at, timeline[-1].authored_at
    weeks = (last - first).days // 7 + 1
    if weeks >= MIN_PERIODS and len(timeline) / weeks >= MIN_WEEKLY_RATE:
        mapping = {commit.sha: (commit.authored_at - first).days // 7 for commit in timeline}
        starts = [first + timedelta(days=7 * index) for index in range(weeks)]
        return mapping, starts, {"period_kind": "week", "period_size": 7}
    size = max(3, len(timeline) // 30)
    mapping = {commit.sha: index // size for index, commit in enumerate(timeline)}
    starts = [timeline[index].authored_at for index in range(0, len(timeline), size)]
    return mapping, starts, {"period_kind": "commit_window", "period_size": size}


def _thresholded(truth: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "precision": round(float(precision_score(truth, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(truth, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(truth, predicted, zero_division=0)), 4),
    }


def _ranked(truth: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    return {
        "roc_auc": round(float(roc_auc_score(truth, scores)), 4),
        "average_precision": round(float(average_precision_score(truth, scores)), 4),
        "brier": round(float(brier_score_loss(truth, np.clip(scores, 0.0, 1.0))), 4),
    }


def _best_threshold(truth: np.ndarray, scores: np.ndarray) -> float:
    candidates = np.unique(np.round(scores, 4))
    best, best_f1 = 0.5, -1.0
    for threshold in candidates:
        value = f1_score(truth, scores >= threshold, zero_division=0)
        if value > best_f1:
            best, best_f1 = float(threshold), float(value)
    return best


def _model() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=0.5, max_iter=2000, random_state=RANDOM_STATE)),
        ]
    )


def train_instability_model(
    commits: list[CommitRecord],
    changes: list[ChangeRecord],
    fix_shas: set[str],
    files: list[FileRecord],
) -> InstabilityResult:
    component_of = group_components(sorted({item.path for item in files}))
    bulk = bulk_commit_shas(changes, len(files))
    changes = [
        change
        for change in changes
        if change.commit_sha not in bulk and change.path in component_of
    ]
    changed_shas = {change.commit_sha for change in changes}
    timeline = sorted(
        (commit for commit in commits if commit.sha in changed_shas),
        key=lambda commit: (commit.authored_at, commit.sha),
    )
    dataset: dict[str, object] = {
        "commits_with_changes": len(timeline),
        "fix_commits": len(fix_shas & changed_shas),
        "bulk_commits_excluded": len(bulk),
        "window": WINDOW,
        "label": "component touched by a bug-fix commit in the next period",
    }
    if len(timeline) < MIN_COMMITS:
        return InstabilityResult(
            MODEL_VERSION,
            "insufficient_data",
            f"Needs at least {MIN_COMMITS} commits that changed analyzed files; "
            f"found {len(timeline)}.",
            dataset,
        )

    period_of, starts, period_info = _assign_periods(timeline)
    periods = len(starts)
    dataset.update(period_info)
    dataset["periods"] = periods
    names = sorted({component_of[change.path] for change in changes})
    index_of = {name: position for position, name in enumerate(names)}
    dataset["components"] = len(names)
    if periods < MIN_PERIODS or len(names) < 2:
        return InstabilityResult(
            MODEL_VERSION,
            "insufficient_data",
            f"Needs at least {MIN_PERIODS} periods and two changed components; found "
            f"{periods} period(s) and {len(names)} component(s).",
            dataset,
        )

    author = {commit.sha: commit.author for commit in timeline}
    touched: dict[tuple[int, int], set[str]] = defaultdict(set)
    churn = np.zeros((len(names), periods))
    for change in changes:
        key = (index_of[component_of[change.path]], period_of[change.commit_sha])
        touched[key].add(change.commit_sha)
        churn[key] += change.churn
    stats = np.zeros((len(names), periods, len(PERIOD_FEATURES)))
    for (component, period), shas in touched.items():
        fixes = len(shas & fix_shas)
        stats[component, period] = [
            len(shas) - fixes,
            fixes,
            math.log1p(churn[component, period]),
            len({author[sha] for sha in shas}),
        ]
    fixed = (stats[:, :, 1] > 0).astype(int)
    sizes: dict[str, int] = defaultdict(int)
    for name in component_of.values():
        sizes[name] += 1

    def row(component: int, period: int) -> list[float]:
        lagged = [stats[component, period - lag] for lag in range(WINDOW)]
        return [
            *np.concatenate(lagged).tolist(),
            float(fixed[component, : period + 1].mean()),
            math.log1p(sizes[names[component]]),
        ]

    targets = list(range(WINDOW, periods))  # periods whose label is predicted
    test_count = max(2, round(len(targets) * 0.25))
    split = targets[-test_count]
    rows, labels, target_period, persistence, rate = [], [], [], [], []
    for period in targets:
        for component in range(len(names)):
            features = row(component, period - 1)
            rows.append(features)
            labels.append(fixed[component, period])
            target_period.append(period)
            persistence.append(float(fixed[component, period - 1]))
            rate.append(features[-2])
    x = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    when = np.asarray(target_period)
    train, test = when < split, when >= split
    dataset.update(
        {
            "train_samples": int(train.sum()),
            "test_samples": int(test.sum()),
            "train_positive": int(y[train].sum()),
            "test_positive": int(y[test].sum()),
            "train_period": [starts[0].isoformat(), starts[split].isoformat()],
            "test_period": [starts[split].isoformat(), timeline[-1].authored_at.isoformat()],
            "features": list(FEATURES),
        }
    )
    if min(y[train].sum(), train.sum() - y[train].sum()) < MIN_CLASS:
        return InstabilityResult(
            MODEL_VERSION,
            "insufficient_data",
            "Too few component-periods with (and without) a bug-fix commit in the training "
            "periods to learn from.",
            dataset,
        )

    model = _model().fit(x[train], y[train])
    threshold = _best_threshold(y[train], model.predict_proba(x[train])[:, 1])
    rate_scores = np.asarray(rate)
    rate_threshold = _best_threshold(y[train], rate_scores[train])
    metrics: dict[str, object] = {"threshold": round(threshold, 4)}
    if min(y[test].sum(), test.sum() - y[test].sum()) >= 1:
        probabilities = model.predict_proba(x[test])[:, 1]
        persist = np.asarray(persistence)[test]
        metrics["model"] = {
            **_ranked(y[test], probabilities),
            **_thresholded(y[test], probabilities >= threshold),
        }
        metrics["baseline_persistence"] = {
            **_ranked(y[test], persist),
            **_thresholded(y[test], persist >= 0.5),
        }
        metrics["baseline_historical_rate"] = {
            **_ranked(y[test], rate_scores[test]),
            **_thresholded(y[test], rate_scores[test] >= rate_threshold),
        }
        metrics["test_base_rate"] = round(float(y[test].mean()), 4)
    else:
        metrics["note"] = "The hold-out periods have a single class, so metrics are unavailable."

    # Deployment: refit on every labelled sample, forecast the period after the last one.
    final = _model().fit(x, y)
    x_now = np.asarray([row(component, periods - 1) for component in range(len(names))])
    probabilities = final.predict_proba(x_now)[:, 1]
    scaler: StandardScaler = final.named_steps["scale"]
    coefficients = final.named_steps["model"].coef_[0]
    contributions = scaler.transform(x_now) * coefficients
    # Absolute bands: with only a handful of components, quantiles would call a 10% forecast
    # "high" simply because it is the largest.
    high, medium = 0.5, 0.25

    evidence: dict[str, list[str]] = defaultdict(list)
    recent: dict[str, list[str]] = defaultdict(list)
    for change in sorted(changes, key=lambda item: item.authored_at, reverse=True):
        name = component_of[change.path]
        bucket = evidence if change.commit_sha in fix_shas else recent
        if change.commit_sha not in bucket[name]:
            bucket[name].append(change.commit_sha)

    predictions = []
    for component, name in enumerate(names):
        probability = float(probabilities[component])
        ranked = sorted(
            zip(FEATURES, contributions[component], strict=True), key=lambda item: -abs(item[1])
        )
        predictions.append(
            ComponentForecast(
                component=name,
                probability=round(probability, 4),
                band="high"
                if probability >= high
                else "medium"
                if probability >= medium
                else "low",
                files=sizes[name],
                recent_periods=[
                    [int(stats[component, period, 0]), int(stats[component, period, 1])]
                    for period in range(periods - WINDOW, periods)
                ],
                fixed_last_period=bool(fixed[component, periods - 1]),
                contributions=[(feature, round(float(value), 4)) for feature, value in ranked[:4]],
                evidence_shas=(evidence[name][:3] + recent[name][:3])[:5],
            )
        )
    predictions.sort(key=lambda item: (-item.probability, item.component))
    dataset["forecast_after"] = timeline[-1].authored_at.isoformat()
    return InstabilityResult(
        model_version=MODEL_VERSION,
        status="trained",
        reason=None,
        dataset=dataset,
        metrics=metrics,
        coefficients=sorted(
            (
                (name, round(float(value), 4))
                for name, value in zip(FEATURES, coefficients, strict=True)
            ),
            key=lambda item: -abs(item[1]),
        ),
        predictions=predictions,
    )
