"""Cross-project defect benchmark over every analysed repository in a workspace.

    uv run python -m code_genome_api.tools.benchmark --workspace ws_benchmark --out docs/benchmarks

Reads each repository's current (branch-head) snapshot, builds the defect model's dataset with
the keyword fix rule, runs the leave-one-repository-out comparison, and writes JSON and Markdown.
"""

import argparse
import json
import logging
from pathlib import Path

from code_genome_ml import keyword_fix_shas
from code_genome_ml.cross_project import RepoDataset, evaluate_cross_project, repository_dataset
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Repository, RepositorySnapshot, utc_now
from ..services.ml import training_inputs

logger = logging.getLogger(__name__)
LABELS = {
    "heuristic": "Heuristic (frequency + churn)",
    "within_logistic_regression": "Within-repo logistic regression",
    "within_random_forest": "Within-repo random forest",
    "within_gradient_boosting": "Within-repo gradient boosting",
    "cross_logistic_regression": "Cross-repo logistic regression",
    "cross_random_forest": "Cross-repo random forest",
    "cross_gradient_boosting": "Cross-repo gradient boosting",
}


def collect(db: Session, workspace_id: str) -> tuple[list[RepoDataset], dict[str, str]]:
    datasets: list[RepoDataset] = []
    skipped: dict[str, str] = {}
    repositories = db.scalars(
        select(Repository)
        .where(Repository.workspace_id == workspace_id)
        .order_by(Repository.external_id)
    )
    for repository in repositories:
        snapshot = db.scalar(
            select(RepositorySnapshot)
            .where(
                RepositorySnapshot.repository_id == repository.id,
                RepositorySnapshot.workspace_id == workspace_id,
                RepositorySnapshot.published_at.is_not(None),
                RepositorySnapshot.as_of.is_(None),
            )
            .order_by(RepositorySnapshot.published_at.desc())
            .limit(1)
        )
        if snapshot is None:
            skipped[repository.external_id] = "no published snapshot"
            continue
        inputs = training_inputs(db, snapshot)
        result = repository_dataset(
            repository.external_id,
            inputs.commits,
            inputs.changes,
            keyword_fix_shas(inputs.commits),
            inputs.files,
            inputs.imports,
        )
        if isinstance(result, str):
            skipped[repository.external_id] = result
        else:
            datasets.append(result)
    return datasets, skipped


def markdown(report: dict[str, object], skipped: dict[str, str]) -> str:
    lines = [
        "# Cross-project defect prediction benchmark",
        "",
        f"Generated {utc_now().date().isoformat()} by `{report['model_version']}`. "
        "Each repository is held out in turn; scores are on its final (test) period. "
        "Labels: a commit matching the "
        "keyword fix rule touched the file. Average precision (AP) is the main metric.",
        "",
    ]
    if report.get("status") != "evaluated":
        lines.append(f"Not evaluated: {report.get('reason')}")
    else:
        mean = report["mean"]
        assert isinstance(mean, dict)
        wins = report["wins"]
        assert isinstance(wins, dict)
        lines += [
            "## Mean over held-out repositories",
            "",
            "| Approach | Mean AP | Mean ROC-AUC | Best on |",
            "|---|---|---|---|",
        ]
        for key, label in LABELS.items():
            lines.append(
                f"| {label} | {mean[key]['average_precision']:.3f} "
                f"| {mean[key]['roc_auc']:.3f} | {wins[key]} |"
            )
        per_repository = report["per_repository"]
        assert isinstance(per_repository, dict)
        lines += [
            "",
            "## Per repository (AP)",
            "",
            "| Repository | Files | Positives | Heuristic | Within LR | Within RF "
            "| Cross LR | Cross RF |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, row in per_repository.items():
            cells = [
                row[key]["average_precision"]
                for key in (
                    "heuristic",
                    "within_logistic_regression",
                    "within_random_forest",
                    "cross_logistic_regression",
                    "cross_random_forest",
                )
            ]
            lines.append(
                f"| {name} | {row['files']} | {row['test_positive']} | "
                + " | ".join(f"{value:.3f}" for value in cells)
                + " |"
            )
        lines += ["", str(report.get("note", ""))]
    if skipped:
        lines += ["", "## Not included", ""] + [
            f"- {name}: {reason}" for name, reason in skipped.items()
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", default="docs/benchmarks", type=Path)
    arguments = parser.parse_args()
    with SessionLocal() as db:
        datasets, skipped = collect(db, arguments.workspace)
    report = evaluate_cross_project(datasets)
    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "defect-cross-project.json").write_text(
        json.dumps({**report, "skipped": skipped}, indent=2) + "\n"
    )
    (arguments.out / "defect-cross-project.md").write_text(markdown(report, skipped))
    print(markdown(report, skipped))


if __name__ == "__main__":
    main()
