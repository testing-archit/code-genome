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

Features: history (commits, churn, contributors, earlier fixes, recency, age, co-change
degree, top-author ownership share), import graph (fan-in, fan-out, betweenness, PageRank)
and, when FILE graph nodes carry them, code metrics (lines of code, cyclomatic complexity
estimate, function count). Missing code metrics are dropped, and ``dataset.code_metrics``
records which were used.

Alternative label (``szz-introducing@1``): when SZZ-lite bug links are supplied, the same
candidates are also trained and scored on "a candidate bug-introducing commit touched the file
in the period" (links with confidence >= 0.5 only, so bulk and shallow-boundary links are
left out). Training labels count only bugs whose fix happened before the test cut-off t2, so
the training set never uses a fix from the period it is scored on. The deployed model keeps
the fix-touch label; the two labels define different ground truth, so their scores are reported
side by side in ``metrics.szz_labels`` rather than used to pick a winner.

Champion/challenger: an L2 logistic regression, a random forest, and a gradient-boosted
tree ensemble are all scored on the test period against the transparent heuristic
baseline. The champion is chosen by average precision (ties favour the simpler model).

Per-file explanations always describe the *champion*, and the result records which
method produced them (``contribution_method``):

* logistic regression: coefficient × standardised feature value, an exact additive
  decomposition of the log-odds (unit: log-odds).
* tree ensembles (random forest, gradient boosting): a local "reset to typical" attribution.
  For each feature, the champion is re-scored with only that feature replaced by its
  median over the labelled files; the contribution is the predicted probability minus
  that re-scored probability (unit: probability points). It shows how much this file's
  value of the feature moves its own prediction. It is not additive: interactions mean
  the values need not sum to the probability.
"""

import importlib.util
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import networkx as nx
import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
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

from .records import (
    BugLinkRecord,
    ChangeRecord,
    CommitRecord,
    FileRecord,
    ImportRecord,
    bulk_commit_shas,
)

MODEL_VERSION = "defect-temporal@3"
RANDOM_STATE = 7
MIN_COMMITS = 24
MIN_CLASS = 3
LABEL_SOURCE = "fix-touch: a commit classified as a bug fix touched the file in the period"
SZZ_LABEL_VERSION = "szz-introducing@1"
SZZ_MIN_CONFIDENCE = 0.5

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
    "betweenness",
    "pagerank",
    "ownership",
)
# Read from FILE graph node properties when the analyzer recorded them. A metric no file
# has is left out of the model (and the dataset says so) rather than filled with zeros.
CODE_METRICS: dict[str, str] = {
    "log_loc": "loc",
    "log_complexity": "complexity",
    "log_functions": "functions",
}

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
    "betweenness": "import-graph betweenness",
    "pagerank": "import-graph PageRank",
    "ownership": "top author's share of commits",
    "log_loc": "lines of code",
    "log_complexity": "cyclomatic complexity (estimate)",
    "log_functions": "function count",
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
    contribution_method: str | None = None
    contribution_unit: str | None = None


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


def import_centrality(
    imports: list[ImportRecord],
) -> tuple[dict[str, float], dict[str, float]]:
    """Public view of the betweenness and PageRank the defect model uses as features."""
    return _centrality(imports)


def _centrality(imports: list[ImportRecord]) -> tuple[dict[str, float], dict[str, float]]:
    """Betweenness (sampled above 400 files) and PageRank on the directed import graph.
    Edges point from importer to imported file, so PageRank rewards widely used files."""
    graph = nx.DiGraph()
    graph.add_edges_from(
        (item.source, item.target) for item in imports if item.source != item.target
    )
    if graph.number_of_nodes() < 3:
        return {}, {}
    sample = None if graph.number_of_nodes() <= 400 else 400
    betweenness = nx.betweenness_centrality(graph, k=sample, normalized=True, seed=RANDOM_STATE)
    pagerank = nx.pagerank(graph, alpha=0.85)
    return betweenness, pagerank


def _code_metrics(files: list[FileRecord]) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Code-metric features available on at least one file, with log-scaled values."""
    available = []
    values: dict[str, dict[str, float]] = {}
    for feature, attribute in CODE_METRICS.items():
        measured = {
            item.path: math.log1p(max(0.0, float(getattr(item, attribute))))
            for item in files
            if isinstance(getattr(item, attribute), int | float)
        }
        if measured:
            available.append(feature)
            values[feature] = measured
    return available, values


def _features(
    paths: list[str],
    history: _History,
    before: datetime | None,
    sizes: dict[str, int],
    fan_in: dict[str, int],
    fan_out: dict[str, int],
    centrality: tuple[dict[str, float], dict[str, float]] = ({}, {}),
    code: dict[str, dict[str, float]] | None = None,
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
        by_author = Counter(history.author.get(sha, "") for sha in shas)
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
                centrality[0].get(path, 0.0),
                centrality[1].get(path, 0.0),
                max(by_author.values()) / len(shas) if shas else 0.0,
            ]
            + [values.get(path, 0.0) for values in (code or {}).values()]
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


def _szz_labels(
    paths: list[str],
    links: list[BugLinkRecord],
    commit_time: dict[str, datetime],
    start: datetime,
    end: datetime | None,
    fixed_before: datetime | None,
) -> np.ndarray:
    """1 when a confident candidate bug-introducing commit in [start, end) touched the file and
    (when ``fixed_before`` is set) the bug was already fixed by then, so no future is used."""
    touched: set[str] = set()
    for link in links:
        introduced = commit_time.get(link.introducing_sha)
        fixed = commit_time.get(link.fix_sha)
        if link.confidence < SZZ_MIN_CONFIDENCE or introduced is None or fixed is None:
            continue
        if introduced < start or (end is not None and introduced >= end):
            continue
        if fixed_before is not None and fixed >= fixed_before:
            continue
        touched.add(link.path)
    return np.asarray([1 if path in touched else 0 for path in paths], dtype=int)


def _szz_comparison(
    paths: list[str],
    links: list[BugLinkRecord],
    commits: list[CommitRecord],
    x_train: np.ndarray,
    x_test: np.ndarray,
    t1: datetime,
    t2: datetime,
) -> dict[str, object]:
    commit_time = {commit.sha: commit.authored_at for commit in commits}
    y_train = _szz_labels(paths, links, commit_time, t1, t2, fixed_before=t2)
    y_test = _szz_labels(paths, links, commit_time, t2, None, fixed_before=None)
    usable = [link for link in links if link.confidence >= SZZ_MIN_CONFIDENCE]
    report: dict[str, object] = {
        "label_version": SZZ_LABEL_VERSION,
        "label": (
            "a candidate bug-introducing commit (SZZ-lite, confidence >= "
            f"{SZZ_MIN_CONFIDENCE}) touched the file in the period; training labels only count "
            "bugs fixed before the test cut-off"
        ),
        "links_supplied": len(links),
        "links_used": len(usable),
        "train_positive": int(y_train.sum()),
        "test_positive": int(y_test.sum()),
    }
    if min(y_train.sum(), len(y_train) - y_train.sum()) < MIN_CLASS:
        report["status"] = "insufficient_data"
        report["reason"] = "Too few files had confident bug-introducing commits before t2."
        return report
    if min(y_test.sum(), len(y_test) - y_test.sum()) < 1:
        report["status"] = "insufficient_data"
        report["reason"] = "The test period has a single class under this label."
        return report
    flag_rate = float(y_train.mean())
    report["status"] = "evaluated"
    report["heuristic_baseline"] = _score(y_test, _heuristic(x_test), flag_rate)
    for name in CANDIDATES:
        model = _build(name)
        model.fit(x_train, y_train)
        report[name] = _score(y_test, model.predict_proba(x_test)[:, 1], flag_rate)
    report["test_base_rate"] = round(float(y_test.mean()), 4)
    return report


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


def _forest() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=3,
        max_features="sqrt",
        class_weight="balanced",
        n_jobs=1,
        random_state=RANDOM_STATE,
    )


# scikit-learn is untyped; candidates share fit/predict_proba.
Candidate = Any
CANDIDATES = ("logistic_regression", "random_forest", "gradient_boosting")
# Tie-break order: prefer the more transparent model when average precision is equal.
_SIMPLICITY = {"logistic_regression": 2, "random_forest": 1, "gradient_boosting": 0}

CONTRIBUTION_METHODS: dict[str, tuple[str, str]] = {
    "logistic_regression": (
        "Logistic-regression coefficient × standardised feature value: an exact additive "
        "decomposition of the model's log-odds.",
        "log-odds",
    ),
    "tree_ensemble": (
        "Local reset-to-typical attribution: the champion's probability for this file minus "
        "its probability with only that feature set to the median of the labelled files. "
        "Values are not additive across features.",
        "probability",
    ),
    "random_forest_shap": (
        "TreeSHAP (shap.TreeExplainer): exact Shapley values of the forest's predicted "
        "probability; they add up to the file's probability minus the average prediction.",
        "probability",
    ),
    "gradient_boosting_shap": (
        "TreeSHAP (shap.TreeExplainer): exact Shapley values of the model's log-odds; they add "
        "up to the file's log-odds minus the average log-odds.",
        "log-odds",
    ),
}


def _shap_contributions(model: Candidate, x_now: np.ndarray, name: str) -> np.ndarray | None:
    """TreeSHAP values for the positive class when the optional ``shap`` package is installed."""
    if importlib.util.find_spec("shap") is None:
        return None
    try:
        import shap  # noqa: PLC0415

        values = np.asarray(shap.TreeExplainer(model).shap_values(x_now))
    except Exception:  # noqa: BLE001 - an unsupported model or version falls back
        return None
    if values.ndim == 3:  # (rows, features, classes)
        values = values[:, :, 1]
    if values.shape != x_now.shape:
        return None
    return values


def _build(name: str) -> Candidate:
    if name == "logistic_regression":
        return _logistic()
    if name == "random_forest":
        return _forest()
    return _boosting()


def _reset_contributions(model: Candidate, x_now: np.ndarray, typical: np.ndarray) -> np.ndarray:
    """Probability change when each feature alone is reset to its typical (median) value."""
    rows, columns = x_now.shape
    base = model.predict_proba(x_now)[:, 1]
    stacked = np.repeat(x_now[np.newaxis, :, :], columns, axis=0)
    for column in range(columns):
        stacked[column, :, column] = typical[column]
    reset = model.predict_proba(stacked.reshape(columns * rows, columns))[:, 1]
    result: np.ndarray = base[:, np.newaxis] - reset.reshape(columns, rows).T
    return result


def _score(truth: np.ndarray, scores: np.ndarray, flag_rate: float) -> dict[str, float]:
    """Ranking metrics plus precision/recall/F1 at a decision rule fixed from training data:
    flag the same share of files as were defect-prone in the training period."""
    k = max(1, math.ceil(len(truth) * 0.2))
    order = np.argsort(-scores, kind="stable")
    top = order[:k]
    flagged = np.zeros(len(truth), dtype=int)
    flagged[order[: max(1, round(len(truth) * flag_rate))]] = 1
    return {
        "roc_auc": round(float(roc_auc_score(truth, scores)), 4),
        "average_precision": round(float(average_precision_score(truth, scores)), 4),
        "precision_at_top20pct": round(float(truth[top].mean()), 4),
        "recall_at_top20pct": round(float(truth[top].sum() / max(1, truth.sum())), 4),
        "precision": round(float(precision_score(truth, flagged, zero_division=0)), 4),
        "recall": round(float(recall_score(truth, flagged, zero_division=0)), 4),
        "f1": round(float(f1_score(truth, flagged, zero_division=0)), 4),
    }


def train_defect_model(
    commits: list[CommitRecord],
    changes: list[ChangeRecord],
    fix_shas: set[str],
    files: list[FileRecord],
    imports: list[ImportRecord],
    bug_links: list[BugLinkRecord] | None = None,
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
        "label_source": LABEL_SOURCE,
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
    centrality = _centrality(imports)
    available, code = _code_metrics(files)
    features = list(FEATURES) + available
    dataset["code_metrics"] = {
        "used": [CODE_METRICS[name] for name in available],
        "missing": [CODE_METRICS[name] for name in CODE_METRICS if name not in available],
        "files_measured": {CODE_METRICS[name]: len(values) for name, values in code.items()},
        "note": (
            "Code metrics come from FILE graph node properties of the analysed snapshot. "
            "Metrics no file carries are left out of the model; files without a value get 0. "
            "Snapshot metrics (and the import graph) are applied to earlier cut-offs too, so "
            "they are slightly anachronistic in the temporal evaluation."
        ),
    }
    t1 = timeline[int(len(timeline) * 0.5)].authored_at
    t2 = timeline[int(len(timeline) * 0.75)].authored_at

    x_train = _features(paths, history, t1, sizes, fan_in, fan_out, centrality, code)
    y_train = _labels(paths, history, t1, t2)
    x_test = _features(paths, history, t2, sizes, fan_in, fan_out, centrality, code)
    y_test = _labels(paths, history, t2, None)
    dataset.update(
        {
            "train_period": [timeline[0].authored_at.isoformat(), t1.isoformat(), t2.isoformat()],
            "test_period": [t2.isoformat(), timeline[-1].authored_at.isoformat()],
            "train_positive": int(y_train.sum()),
            "train_negative": int(len(y_train) - y_train.sum()),
            "test_positive": int(y_test.sum()),
            "test_negative": int(len(y_test) - y_test.sum()),
            "features": features,
        }
    )
    if min(y_train.sum(), len(y_train) - y_train.sum()) < MIN_CLASS:
        return DefectResult(
            MODEL_VERSION,
            "insufficient_data",
            "Too few files were touched by bug-fix commits in the training period to learn from.",
            dataset,
        )

    candidates = {name: _build(name) for name in CANDIDATES}
    for model in candidates.values():
        model.fit(x_train, y_train)

    metrics: dict[str, object] = {}
    champion = "logistic_regression"
    calibration: dict[str, list[float]] = {}
    importance: list[tuple[str, float]] = []
    evaluable = min(y_test.sum(), len(y_test) - y_test.sum()) >= 1
    if evaluable:
        flag_rate = float(y_train.mean())
        metrics["decision_rule"] = (
            f"Precision, recall and F1 flag the top {flag_rate:.0%} of files, the share that "
            "was defect-prone in the training period (fixed before seeing the test period)."
        )
        metrics["heuristic_baseline"] = _score(y_test, _heuristic(x_test), flag_rate)
        for name, model in candidates.items():
            probabilities = model.predict_proba(x_test)[:, 1]
            scores = _score(y_test, probabilities, flag_rate)
            scores["brier"] = round(float(brier_score_loss(y_test, probabilities)), 4)
            metrics[name] = scores
        champion = max(
            candidates,
            key=lambda name: (metrics[name]["average_precision"], _SIMPLICITY[name]),  # type: ignore[index]
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
                for name, value in zip(features, permutation.importances_mean, strict=True)
            ),
            key=lambda item: -item[1],
        )
        metrics["test_base_rate"] = round(float(y_test.mean()), 4)
    else:
        metrics["note"] = "The test period has a single class, so held-out metrics are unavailable."

    if bug_links:
        metrics["szz_labels"] = _szz_comparison(paths, bug_links, commits, x_train, x_test, t1, t2)

    # Deployment model: refit the champion on both labelled periods.
    x_all = np.vstack([x_train, x_test])
    y_all = np.concatenate([y_train, y_test])
    final = _build(champion)
    final.fit(x_all, y_all)
    x_now = _features(paths, history, None, sizes, fan_in, fan_out, centrality, code)
    probabilities = final.predict_proba(x_now)[:, 1]
    if isinstance(final, Pipeline):
        scaler: StandardScaler = final.named_steps["scale"]
        coefficients = final.named_steps["model"].coef_[0]
        contributions = scaler.transform(x_now) * coefficients
        method, unit = CONTRIBUTION_METHODS["logistic_regression"]
    else:
        explained = _shap_contributions(final, x_now, champion)
        if explained is not None:
            contributions = explained
            method, unit = CONTRIBUTION_METHODS[f"{champion}_shap"]
        else:
            contributions = _reset_contributions(final, x_now, np.median(x_all, axis=0))
            method, unit = CONTRIBUTION_METHODS["tree_ensemble"]
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
            zip(features, contributions[index], strict=True), key=lambda item: -abs(item[1])
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
                    for name, value in zip(features, x_now[index], strict=True)
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
        contribution_method=method,
        contribution_unit=unit,
    )
