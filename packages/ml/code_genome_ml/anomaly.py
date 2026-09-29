"""Unusual-commit detection with an Isolation Forest.

Each commit is described by its shape: files touched, lines churned, directories
spanned, message length, whether it is a merge, and its UTC hour and weekday. Commits
that the forest isolates quickly are flagged, and each flag lists the features furthest
from the repository's median (robust z-scores) so a reviewer can see why.
Unusual is not wrong: a flag is a prompt for review, never a finding.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import IsolationForest

from .records import ChangeRecord, CommitRecord

MODEL_VERSION = "commit-isolation-forest@1"
FEATURES = (
    "files_touched",
    "lines_churned",
    "directories",
    "message_words",
    "merge",
    "hour_utc",
    "weekend",
)
LABELS = {
    "files_touched": "files touched",
    "lines_churned": "lines churned",
    "directories": "top-level directories spanned",
    "message_words": "message length",
    "merge": "merge commit",
    "hour_utc": "time of day (UTC)",
    "weekend": "weekend commit",
}


@dataclass
class CommitAnomaly:
    sha: str
    score: float
    reasons: list[str]


@dataclass
class AnomalyResult:
    model_version: str
    status: str
    reason: str | None
    metrics: dict[str, object] = field(default_factory=dict)
    anomalies: list[CommitAnomaly] = field(default_factory=list)


def detect_anomalies(commits: list[CommitRecord], changes: list[ChangeRecord]) -> AnomalyResult:
    per_commit: dict[str, list[ChangeRecord]] = defaultdict(list)
    for change in changes:
        per_commit[change.commit_sha].append(change)
    rows = []
    shas = []
    raw = []
    for commit in commits:
        items = per_commit.get(commit.sha, [])
        files = len({item.path for item in items})
        churn = sum(item.churn for item in items)
        directories = len({item.path.split("/")[0] for item in items})
        words = len(commit.message.split())
        hour = commit.authored_at.hour
        values = [
            files,
            churn,
            directories,
            words,
            1 if commit.parent_count > 1 else 0,
            hour,
            1 if commit.authored_at.weekday() >= 5 else 0,
        ]
        raw.append(values)
        rows.append(
            [
                math.log1p(files),
                math.log1p(churn),
                directories,
                math.log1p(words),
                values[4],
                math.sin(2 * math.pi * hour / 24),
                values[6],
            ]
        )
        shas.append(commit.sha)
    if len(rows) < 20:
        return AnomalyResult(
            MODEL_VERSION, "insufficient_data", f"Needs at least 20 commits; found {len(rows)}."
        )
    matrix = np.asarray(rows, dtype=float)
    forest = IsolationForest(n_estimators=200, contamination=0.05, random_state=7).fit(matrix)
    scores = -forest.score_samples(matrix)
    flagged = np.where(forest.predict(matrix) == -1)[0]
    raw_matrix = np.asarray(raw, dtype=float)
    median = np.median(raw_matrix, axis=0)
    mad = np.median(np.abs(raw_matrix - median), axis=0) * 1.4826
    anomalies = []
    for index in sorted(flagged, key=lambda i: -scores[i])[:25]:
        deviations = []
        for column, name in enumerate(FEATURES):
            spread = mad[column] if mad[column] > 0 else max(1.0, abs(median[column]) * 0.5)
            z = (raw_matrix[index, column] - median[column]) / spread
            if name in {"merge", "weekend"}:
                if raw_matrix[index, column] and np.mean(raw_matrix[:, column]) < 0.2:
                    deviations.append((3.0, LABELS[name]))
            elif name == "hour_utc":
                continue
            elif abs(z) >= 2.5:
                value = int(raw_matrix[index, column])
                direction = "high" if z > 0 else "low"
                deviations.append(
                    (
                        abs(z),
                        f"{LABELS[name]} {direction} ({value} vs median {int(median[column])})",
                    )
                )
        deviations.sort(reverse=True)
        anomalies.append(
            CommitAnomaly(
                shas[index],
                round(float(scores[index]), 4),
                [text for _, text in deviations[:3]] or ["unusual combination of features"],
            )
        )
    return AnomalyResult(
        MODEL_VERSION,
        "trained",
        None,
        metrics={"commits": len(rows), "flagged": len(anomalies), "contamination": 0.05},
        anomalies=anomalies,
    )
