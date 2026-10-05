"""Train every repository model for one snapshot and return JSON-ready results."""

import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .anomaly import detect_anomalies
from .defect import FEATURE_LABELS as DEFECT_FEATURE_LABELS
from .defect import train_defect_model
from .impact import FEATURE_LABELS as IMPACT_FEATURE_LABELS
from .impact import train_link_model
from .instability import train_instability_model
from .intent import train_intent_classifier
from .modules import discover_modules
from .records import ChangeRecord, CommitRecord, FileRecord, ImportRecord, SearchDocument
from .retrieval import MODEL_VERSION as RETRIEVAL_VERSION
from .retrieval import evaluate_retrieval

TASKS = (
    "commit_intent",
    "defect_risk",
    "change_impact",
    "retrieval",
    "modules",
    "anomalies",
    "instability",
)


@dataclass(frozen=True)
class TrainingInputs:
    commits: list[CommitRecord]
    changes: list[ChangeRecord]
    files: list[FileRecord]
    imports: list[ImportRecord]


def file_document(record: FileRecord) -> SearchDocument:
    symbols = " ".join(record.symbols[:60])
    identifier = f"evidence:{record.evidence_id}" if record.evidence_id else f"file:{record.path}"
    return SearchDocument(
        id=identifier,
        kind="file",
        text=f"{record.path} {record.path} {symbols}",
        title=record.path,
        path=record.path,
    )


def _timed(function: Any, *args: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    result = function(*args)
    return result, round(time.perf_counter() - start, 3)


def train_all(inputs: TrainingInputs) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}

    intent, seconds = _timed(train_intent_classifier, inputs.commits)
    payload = asdict(intent)
    payload["training_seconds"] = seconds
    results["commit_intent"] = payload
    fix_shas = {item.sha for item in intent.predictions if item.intent == "fix"}

    defect, seconds = _timed(
        train_defect_model, inputs.commits, inputs.changes, fix_shas, inputs.files, inputs.imports
    )
    payload = asdict(defect)
    payload["training_seconds"] = seconds
    payload["feature_labels"] = DEFECT_FEATURE_LABELS
    results["defect_risk"] = payload

    instability, seconds = _timed(
        train_instability_model, inputs.commits, inputs.changes, fix_shas, inputs.files
    )
    payload = asdict(instability)
    payload["training_seconds"] = seconds
    results["instability"] = payload

    link, seconds = _timed(train_link_model, inputs.changes, inputs.imports, len(inputs.files))
    payload = asdict(link)
    payload["training_seconds"] = seconds
    payload["feature_labels"] = IMPACT_FEATURE_LABELS
    results["change_impact"] = payload

    analyzable = {record.path for record in inputs.files}
    changed_by_commit: dict[str, set[str]] = defaultdict(set)
    for change in inputs.changes:
        if change.path in analyzable:
            changed_by_commit[change.commit_sha].add(change.path)
    queries = [
        (commit.message.splitlines()[0] if commit.message else "", changed_by_commit[commit.sha])
        for commit in inputs.commits
        if 0 < len(changed_by_commit.get(commit.sha, ())) <= 10
    ]
    retrieval, seconds = _timed(
        evaluate_retrieval, [file_document(record) for record in inputs.files], queries
    )
    results["retrieval"] = {
        "model_version": RETRIEVAL_VERSION,
        "status": "trained" if retrieval.get("status") == "evaluated" else "insufficient_data",
        "reason": None
        if retrieval.get("status") == "evaluated"
        else "Needs at least five commits that changed analyzable files.",
        "metrics": retrieval,
        "training_seconds": seconds,
    }

    modules, seconds = _timed(discover_modules, sorted(analyzable), inputs.changes, inputs.imports)
    payload = asdict(modules)
    payload["training_seconds"] = seconds
    results["modules"] = payload

    anomalies, seconds = _timed(detect_anomalies, inputs.commits, inputs.changes)
    payload = asdict(anomalies)
    payload["training_seconds"] = seconds
    results["anomalies"] = payload
    return results
