import re
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..auth import Actor, Database
from ..errors import AppError
from ..models import (
    AnalysisRun,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    ModuleCandidate,
    RepositorySnapshot,
    utc_now,
)
from ..schemas import (
    ComparedFileResponse,
    ComparedHotspotResponse,
    ComparedImportResponse,
    ComparedModuleResponse,
    ComparisonCounts,
    SnapshotComparisonResponse,
    SnapshotSummaryResponse,
)
from .intelligence import _repository

router = APIRouter(tags=["snapshots"])

_SHA = re.compile(r"^[0-9a-f]{7,64}$")
LIST_CAP = 500
HOTSPOT_CAP = 25


def _summary(db: Database, snapshot: RepositorySnapshot) -> SnapshotSummaryResponse:
    run = db.get(AnalysisRun, snapshot.run_id)
    assert snapshot.published_at is not None
    return SnapshotSummaryResponse(
        id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        tree_sha=snapshot.tree_sha,
        analysis_version=snapshot.analysis_version,
        run_id=snapshot.run_id,
        refs=list(run.requested_refs) if run else [],
        published_at=snapshot.published_at,
        as_of=snapshot.as_of,
    )


def _published(db: Database, repository_id: str, actor: Actor) -> list[RepositorySnapshot]:
    return list(
        db.scalars(
            select(RepositorySnapshot)
            .where(
                RepositorySnapshot.repository_id == repository_id,
                RepositorySnapshot.workspace_id == actor.workspace_id,
                RepositorySnapshot.published_at.is_not(None),
            )
            .order_by(RepositorySnapshot.published_at.desc())
        )
    )


def resolve_snapshot(
    db: Database, repository_id: str, actor: Actor, sha: str
) -> RepositorySnapshot:
    """Resolve a full or abbreviated commit SHA to one published snapshot in this workspace."""
    sha = sha.strip().lower()
    if not _SHA.match(sha):
        raise AppError(
            400, "INVALID_SCOPE", "Invalid snapshot", "Snapshot SHAs are 7-64 hex characters."
        )
    matches = [
        item for item in _published(db, repository_id, actor) if item.commit_sha.startswith(sha)
    ]
    if not matches:
        raise AppError(404, "NOT_FOUND", "Snapshot not found", "No published snapshot matches.")
    if len(matches) > 1:
        raise AppError(
            400, "INVALID_SCOPE", "Ambiguous snapshot", "Use a longer SHA; several snapshots match."
        )
    return matches[0]


def _manifest(db: Database, snapshot: RepositorySnapshot) -> dict[str, tuple[str, int]]:
    return {
        row.path: (row.blob_sha, row.size)
        for row in db.scalars(
            select(FileManifestEntry).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == snapshot.workspace_id,
            )
        )
    }


def _imports(db: Database, snapshot: RepositorySnapshot) -> dict[tuple[str, str], str]:
    nodes = {
        row.id: row.natural_key
        for row in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
            )
        )
    }
    edges: dict[tuple[str, str], str] = {}
    for edge in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == snapshot.workspace_id,
            GraphEdge.type == "IMPORTS",
        )
    ):
        source, target = nodes.get(edge.from_node), nodes.get(edge.to_node)
        if source is not None and target is not None:
            edges.setdefault((source, target), f"evidence:{edge.provenance_id}")
    return edges


def _modules(db: Database, snapshot: RepositorySnapshot) -> dict[str, ModuleCandidate]:
    return {
        row.natural_key: row
        for row in db.scalars(
            select(ModuleCandidate).where(
                ModuleCandidate.snapshot_id == snapshot.id,
                ModuleCandidate.workspace_id == snapshot.workspace_id,
            )
        )
    }


def _hotspots(db: Database, snapshot: RepositorySnapshot) -> dict[str, float]:
    return {
        row.path: row.score
        for row in db.scalars(
            select(FileHotspot).where(
                FileHotspot.snapshot_id == snapshot.id,
                FileHotspot.workspace_id == snapshot.workspace_id,
            )
        )
    }


@router.get("/repositories/{repository_id}/snapshots", response_model=list[SnapshotSummaryResponse])
def list_snapshots(repository_id: str, db: Database, actor: Actor) -> list[SnapshotSummaryResponse]:
    _repository(db, repository_id, actor)
    return [_summary(db, item) for item in _published(db, repository_id, actor)]


def compare_snapshots(
    db: Database, repository_id: str, actor: Actor, base_sha: str, head_sha: str
) -> SnapshotComparisonResponse:
    _repository(db, repository_id, actor)
    base = resolve_snapshot(db, repository_id, actor, base_sha)
    head = resolve_snapshot(db, repository_id, actor, head_sha)
    if base.id == head.id:
        raise AppError(
            400, "INVALID_SCOPE", "Same snapshot", "Choose two different snapshots to compare."
        )

    base_files, head_files = _manifest(db, base), _manifest(db, head)
    base_imports, head_imports = _imports(db, base), _imports(db, head)
    base_modules, head_modules = _modules(db, base), _modules(db, head)
    base_hot, head_hot = _hotspots(db, base), _hotspots(db, head)
    # Older snapshots can predate a data class entirely. Comparing an empty side would
    # report everything as added or removed, so such sections are marked unavailable.
    unavailable = [
        name
        for name, left, right in (
            ("files", base_files, head_files),
            ("imports", base_imports, head_imports),
            ("modules", base_modules, head_modules),
            ("hotspots", base_hot, head_hot),
        )
        if not left or not right
    ]
    if "files" in unavailable:
        base_files = head_files = {}
    if "imports" in unavailable:
        base_imports = head_imports = {}
    if "modules" in unavailable:
        base_modules = head_modules = {}
    if "hotspots" in unavailable:
        base_hot = head_hot = {}
    added = [
        ComparedFileResponse(path=path, base_blob_sha=None, head_blob_sha=blob, size_delta=size)
        for path, (blob, size) in sorted(head_files.items())
        if path not in base_files
    ]
    removed = [
        ComparedFileResponse(path=path, base_blob_sha=blob, head_blob_sha=None, size_delta=-size)
        for path, (blob, size) in sorted(base_files.items())
        if path not in head_files
    ]
    modified = [
        ComparedFileResponse(
            path=path,
            base_blob_sha=base_files[path][0],
            head_blob_sha=blob,
            size_delta=size - base_files[path][1],
        )
        for path, (blob, size) in sorted(head_files.items())
        if path in base_files and base_files[path][0] != blob
    ]

    imports_added = [
        ComparedImportResponse(source=source, target=target, evidence_id=evidence)
        for (source, target), evidence in sorted(head_imports.items())
        if (source, target) not in base_imports
    ]
    imports_removed = [
        ComparedImportResponse(source=source, target=target, evidence_id=evidence)
        for (source, target), evidence in sorted(base_imports.items())
        if (source, target) not in head_imports
    ]

    modules: list[ComparedModuleResponse] = []
    for name in sorted(set(base_modules) | set(head_modules)):
        before = set(base_modules[name].file_paths) if name in base_modules else set()
        after = set(head_modules[name].file_paths) if name in head_modules else set()
        if name in base_modules and name in head_modules and before == after:
            continue
        status = (
            "added"
            if name not in base_modules
            else "removed"
            if name not in head_modules
            else "changed"
        )
        modules.append(
            ComparedModuleResponse(
                name=name,
                status=status,
                added_files=sorted(after - before)[:LIST_CAP],
                removed_files=sorted(before - after)[:LIST_CAP],
                inferred=(head_modules.get(name) or base_modules[name]).inferred,
            )
        )

    hotspots = sorted(
        (
            ComparedHotspotResponse(
                path=path,
                base_score=base_hot.get(path),
                head_score=head_hot.get(path),
                delta=round(head_hot.get(path, 0.0) - base_hot.get(path, 0.0), 4),
            )
            for path in set(base_hot) | set(head_hot)
        ),
        key=lambda item: (-abs(item.delta), item.path),
    )
    hotspots = [item for item in hotspots if item.delta != 0][:HOTSPOT_CAP]

    limitations = [
        "Files are compared by Git blob SHA from each snapshot's manifest; content is not diffed.",
        "Imports are compared by file and module key; renamed files appear as removed and added.",
        "Module boundaries in both snapshots are inferred.",
        "Hotspot scores are relative within each snapshot, so deltas show direction, "
        "not absolute change in defect likelihood.",
    ]
    for name in unavailable:
        limitations.append(
            f"{name.capitalize()} were not compared: at least one snapshot has no {name} "
            "records, usually because it was produced by an earlier analyser."
        )
    if base.published_at and head.published_at and base.published_at > head.published_at:
        limitations.append("The base snapshot was published after the head snapshot.")
    if base.analysis_version != head.analysis_version:
        limitations.append(
            f"Snapshots were produced by different analysis versions ({base.analysis_version} "
            f"and {head.analysis_version}); some differences may come from the analyser."
        )
    truncated = [
        name
        for name, items in (
            ("added files", added),
            ("removed files", removed),
            ("modified files", modified),
            ("added imports", imports_added),
            ("removed imports", imports_removed),
        )
        if len(items) > LIST_CAP
    ]
    if truncated:
        limitations.append(f"Lists are limited to {LIST_CAP} entries: {', '.join(truncated)}.")

    return SnapshotComparisonResponse(
        repository_id=repository_id,
        base=_summary(db, base),
        head=_summary(db, head),
        generated_at=utc_now(),
        unavailable=unavailable,
        counts=ComparisonCounts(
            files_added=len(added),
            files_removed=len(removed),
            files_modified=len(modified),
            imports_added=len(imports_added),
            imports_removed=len(imports_removed),
            modules_changed=len(modules),
        ),
        files_added=added[:LIST_CAP],
        files_removed=removed[:LIST_CAP],
        files_modified=modified[:LIST_CAP],
        imports_added=imports_added[:LIST_CAP],
        imports_removed=imports_removed[:LIST_CAP],
        modules=modules,
        hotspots=hotspots,
        limitations=limitations,
    )


@router.get("/repositories/{repository_id}/compare", response_model=SnapshotComparisonResponse)
def get_comparison(
    repository_id: str,
    db: Database,
    actor: Actor,
    base: Annotated[str, Query(min_length=7, max_length=64)],
    head: Annotated[str, Query(min_length=7, max_length=64)],
) -> SnapshotComparisonResponse:
    return compare_snapshots(db, repository_id, actor, base, head)
