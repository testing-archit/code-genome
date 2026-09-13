from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..auth import Actor, Database
from ..errors import AppError
from ..models import CoChangeEdge, FileHotspot, ModuleCandidate, Repository, RepositorySnapshot
from ..schemas import (
    ArchitectureCoChangeResponse,
    ArchitectureHotspotResponse,
    ArchitectureModuleResponse,
    ArchitectureResponse,
)

router = APIRouter(tags=["architecture"])


@router.get("/repositories/{repository_id}/architecture", response_model=ArchitectureResponse)
def get_architecture(
    repository_id: str,
    db: Database,
    actor: Actor,
    relationship_limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ArchitectureResponse:
    repository = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == actor.workspace_id
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")
    snapshot = db.scalar(
        select(RepositorySnapshot)
        .where(
            RepositorySnapshot.repository_id == repository_id,
            RepositorySnapshot.workspace_id == actor.workspace_id,
            RepositorySnapshot.published_at.is_not(None),
        )
        .order_by(RepositorySnapshot.published_at.desc())
        .limit(1)
    )
    if snapshot is None:
        raise AppError(404, "NOT_FOUND", "Architecture not found", "No published snapshot exists.")
    modules = list(
        db.scalars(
            select(ModuleCandidate)
            .where(ModuleCandidate.snapshot_id == snapshot.id)
            .order_by(ModuleCandidate.natural_key)
        )
    )
    hotspots = list(
        db.scalars(
            select(FileHotspot)
            .where(FileHotspot.snapshot_id == snapshot.id)
            .order_by(FileHotspot.score.desc())
            .limit(20)
        )
    )
    co_changes = list(
        db.scalars(
            select(CoChangeEdge)
            .where(CoChangeEdge.snapshot_id == snapshot.id)
            .order_by(CoChangeEdge.commit_count.desc(), CoChangeEdge.left_path)
            .limit(relationship_limit)
        )
    )
    if not modules:
        raise AppError(
            404,
            "NOT_FOUND",
            "Architecture not found",
            "Evolutionary analysis is not available for this snapshot.",
        )
    version = modules[0].analysis_version
    return ArchitectureResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha,
        analysis_version=version,
        modules=[
            ArchitectureModuleResponse(
                id=item.id,
                name=item.natural_key,
                file_paths=item.file_paths,
                confidence=item.confidence,
                description=item.description,
                citations=[f"commit:{sha}" for sha in item.evidence_shas],
                inferred=item.inferred,
            )
            for item in modules
        ],
        hotspots=[
            ArchitectureHotspotResponse(
                path=item.path,
                commit_count=item.commit_count,
                churn=item.churn,
                score=item.score,
                citations=[f"commit:{sha}" for sha in item.evidence_shas],
            )
            for item in hotspots
        ],
        co_changes=[
            ArchitectureCoChangeResponse(
                left_path=item.left_path,
                right_path=item.right_path,
                commit_count=item.commit_count,
                confidence=item.confidence,
                citations=[f"commit:{sha}" for sha in item.evidence_shas],
            )
            for item in co_changes
        ],
        limitations=[
            "Module boundaries are inferred from current directory structure and observed history.",
            "Hotspot scores are relative change-frequency signals, not defect probabilities.",
            f"Co-change relationships are limited to {relationship_limit} records.",
        ],
    )
