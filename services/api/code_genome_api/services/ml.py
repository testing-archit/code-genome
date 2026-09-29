"""Bridges repository evidence in the database to the ML models in ``code_genome_ml``."""

import logging
from collections import defaultdict
from typing import Any

from code_genome_ml import (
    TASKS,
    ChangeRecord,
    CommitRecord,
    FileRecord,
    HybridRetriever,
    ImportRecord,
    SearchDocument,
    TrainingInputs,
    file_document,
    train_all,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..ids import new_id
from ..models import (
    FileChange,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    MlModelRun,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)

logger = logging.getLogger(__name__)
MAX_STORED_PREDICTIONS = 500


def training_inputs(db: Session, snapshot: RepositorySnapshot) -> TrainingInputs:
    commits = [
        CommitRecord(
            sha=row.sha,
            message=row.message,
            author=row.author_name,
            authored_at=row.authored_at,
            parent_count=len(row.parent_shas),
        )
        for row in db.scalars(
            select(RepositoryCommit).where(
                RepositoryCommit.repository_id == snapshot.repository_id,
                RepositoryCommit.workspace_id == snapshot.workspace_id,
            )
        )
    ]
    changes = [
        ChangeRecord(row.commit_sha, row.path, row.authored_at, row.churn)
        for row in db.scalars(
            select(FileChange).where(
                FileChange.snapshot_id == snapshot.id,
                FileChange.workspace_id == snapshot.workspace_id,
            )
        )
    ]
    nodes = {
        node.id: node
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
            )
        )
    }
    symbols: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        if node.kind == "SYMBOL":
            path = str(node.properties_json.get("path") or node.natural_key.split("#", 1)[0])
            name = node.properties_json.get("name")
            if isinstance(name, str):
                symbols[path].append(name)
    file_nodes = {node.natural_key: node for node in nodes.values() if node.kind == "FILE"}
    sizes = {
        row.path: row.size
        for row in db.scalars(
            select(FileManifestEntry).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == snapshot.workspace_id,
                FileManifestEntry.analyzed.is_(True),
            )
        )
    }
    paths = sorted(set(file_nodes) | set(sizes))
    files = [
        FileRecord(
            path=path,
            size=sizes.get(path, 0),
            symbols=tuple(sorted(set(symbols.get(path, [])))),
            evidence_id=(
                file_nodes[path].evidence_ids[0]
                if path in file_nodes and file_nodes[path].evidence_ids
                else None
            ),
        )
        for path in paths
    ]
    imports = []
    for edge in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == snapshot.workspace_id,
            GraphEdge.type == "IMPORTS",
        )
    ):
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source and target and source.kind == "FILE" and target.kind == "FILE":
            imports.append(ImportRecord(source.natural_key, target.natural_key))
    return TrainingInputs(commits, changes, files, imports)


def _trim(task: str, result: dict[str, Any]) -> dict[str, Any]:
    if task == "defect_risk":
        result["predictions"] = result.get("predictions", [])[:MAX_STORED_PREDICTIONS]
    return result


def train_snapshot_models(
    db: Session, snapshot: RepositorySnapshot, trained_by: str
) -> list[MlModelRun]:
    """Train all models for a snapshot and replace its previous runs in one transaction.

    The caller commits. Nothing is replaced if training raises."""
    results = train_all(training_inputs(db, snapshot))
    db.execute(
        delete(MlModelRun).where(
            MlModelRun.snapshot_id == snapshot.id,
            MlModelRun.workspace_id == snapshot.workspace_id,
        )
    )
    runs = []
    for task in TASKS:
        result = _trim(task, results[task])
        run = MlModelRun(
            id=new_id("mlr"),
            workspace_id=snapshot.workspace_id,
            repository_id=snapshot.repository_id,
            snapshot_id=snapshot.id,
            snapshot_sha=snapshot.commit_sha,
            task=task,
            model_version=str(result.get("model_version", "unknown")),
            status=str(result.get("status", "unknown")),
            result_json=result,
            trained_by=trained_by,
        )
        db.add(run)
        runs.append(run)
    _primed.clear()
    return runs


def model_runs(db: Session, snapshot: RepositorySnapshot) -> dict[str, MlModelRun]:
    return {
        run.task: run
        for run in db.scalars(
            select(MlModelRun).where(
                MlModelRun.snapshot_id == snapshot.id,
                MlModelRun.workspace_id == snapshot.workspace_id,
            )
        )
    }


def trained_result(db: Session, snapshot: RepositorySnapshot, task: str) -> dict[str, Any] | None:
    run = db.scalar(
        select(MlModelRun).where(
            MlModelRun.snapshot_id == snapshot.id,
            MlModelRun.workspace_id == snapshot.workspace_id,
            MlModelRun.task == task,
            MlModelRun.status == "trained",
        )
    )
    return run.result_json if run else None


def search_documents(db: Session, snapshot: RepositorySnapshot) -> list[SearchDocument]:
    """Everything a question can be answered from: files, modules, hotspots, commits."""
    inputs = training_inputs(db, snapshot)
    documents = [file_document(record) for record in inputs.files]
    for module in db.scalars(
        select(ModuleCandidate).where(
            ModuleCandidate.snapshot_id == snapshot.id,
            ModuleCandidate.workspace_id == snapshot.workspace_id,
        )
    ):
        documents.append(
            SearchDocument(
                id=f"module:{module.id}",
                kind="module",
                text=(
                    f"Module {module.natural_key}: {module.description} "
                    f"{' '.join(module.file_paths[:40])}"
                ),
                title=module.natural_key,
            )
        )
    for hotspot in db.scalars(
        select(FileHotspot).where(
            FileHotspot.snapshot_id == snapshot.id,
            FileHotspot.workspace_id == snapshot.workspace_id,
        )
    ):
        documents.append(
            SearchDocument(
                id=f"hotspot:{hotspot.path}",
                kind="hotspot",
                text=(
                    f"File {hotspot.path} is a relative change hotspot with "
                    f"{hotspot.commit_count} commits."
                ),
                title=hotspot.path,
                path=hotspot.path,
            )
        )
    for commit in sorted(inputs.commits, key=lambda item: item.authored_at, reverse=True)[:500]:
        documents.append(
            SearchDocument(
                id=f"commit:{commit.sha}",
                kind="commit",
                text=commit.message,
                title=commit.message.splitlines()[0][:160] if commit.message else commit.sha[:12],
            )
        )
    return documents


# Retrievers keyed by (snapshot, workspace, commit); bounded to the 16 most recent.
_primed: dict[tuple[str, str, str], HybridRetriever] = {}


def retrieval_mode(db: Session, snapshot: RepositorySnapshot) -> str:
    """The ranking mode that won this snapshot's retrieval evaluation (hybrid if untrained)."""
    result = trained_result(db, snapshot, "retrieval")
    mode = (result or {}).get("metrics", {}).get("selected_mode")
    return mode if mode in {"hybrid", "bm25", "semantic"} else "hybrid"


def retriever(db: Session, snapshot: RepositorySnapshot) -> HybridRetriever:
    """Hybrid retriever for a snapshot, built once per process and snapshot."""
    key = (snapshot.id, snapshot.workspace_id, snapshot.commit_sha)
    cached = _primed.get(key)
    if cached is None:
        cached = HybridRetriever(search_documents(db, snapshot))
        if len(_primed) >= 16:
            _primed.pop(next(iter(_primed)))
        _primed[key] = cached
    return cached
