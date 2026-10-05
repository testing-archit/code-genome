from typing import Annotated

from code_genome_ml import RETRIEVAL_VERSION
from fastapi import APIRouter, Query, Request

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..models import MlModelRun
from ..schemas import (
    MlOverviewResponse,
    MlTaskResponse,
    SearchHitResponse,
    SearchResponse,
)
from ..services import ml
from .intelligence import _repository, _snapshot

router = APIRouter(tags=["machine-learning"])
NO_EVIDENCE = "No supporting evidence was identified in the selected scope."
LIMITATIONS = [
    "Models are trained per repository on its own history; metrics come from a held-out, "
    "later time period or cross-validation and describe this repository only.",
    "Predictions rank and prioritise. They do not establish that code is defective, "
    "complete, tested, or deployed.",
    "Tasks marked insufficient_data abstained because the history was too short to "
    "evaluate honestly.",
]


def _overview(
    repository_id: str, snapshot_sha: str, runs: dict[str, MlModelRun]
) -> MlOverviewResponse:
    return MlOverviewResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot_sha,
        trained=bool(runs),
        tasks={
            task: MlTaskResponse(
                task=task,
                model_version=run.model_version,
                status=run.status,
                trained_at=run.trained_at,
                result=run.result_json,
            )
            for task, run in runs.items()
        },
        limitations=LIMITATIONS,
    )


@router.get("/repositories/{repository_id}/ml", response_model=MlOverviewResponse)
def get_models(repository_id: str, db: Database, actor: Actor) -> MlOverviewResponse:
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    return _overview(repository_id, snapshot.commit_sha, ml.model_runs(db, snapshot))


@router.post("/repositories/{repository_id}/ml/train", response_model=MlOverviewResponse)
def train_models(
    repository_id: str, request: Request, db: Database, actor: Actor
) -> MlOverviewResponse:
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    runs = ml.train_snapshot_models(db, snapshot, actor.user_id)
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="ml_models.trained",
        resource_type="repository_snapshot",
        resource_id=snapshot.id,
        request_id=request.state.request_id,
    )
    db.commit()
    return _overview(repository_id, snapshot.commit_sha, {run.task: run for run in runs})


@router.get("/repositories/{repository_id}/search", response_model=SearchResponse)
def search_repository(
    repository_id: str,
    db: Database,
    actor: Actor,
    q: str = Query(min_length=2, max_length=500),
    kind: Annotated[list[str] | None, Query(max_length=4)] = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> SearchResponse:
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    allowed = {
        item
        for item in kind or []
        if item in {"file", "module", "hotspot", "commit", "doc", "code"}
    } or None
    mode = ml.retrieval_mode(db, snapshot)
    hits = ml.retriever(db, snapshot).search(q, limit=limit, kinds=allowed, mode=mode)
    return SearchResponse(
        query=q,
        snapshot_sha=snapshot.commit_sha,
        model_version=f"{RETRIEVAL_VERSION}:{mode}",
        hits=[
            SearchHitResponse(
                id=hit.document.id,
                kind=hit.document.kind,
                title=hit.document.title,
                path=hit.document.path,
                score=hit.score,
                bm25=hit.bm25,
                semantic=hit.semantic,
                bm25_rank=hit.bm25_rank,
                semantic_rank=hit.semantic_rank,
                match=(
                    "keyword and semantic"
                    if hit.bm25_rank and hit.semantic_rank
                    else "keyword"
                    if hit.bm25_rank
                    else "semantic (inferred)"
                ),
            )
            for hit in hits
        ],
        message=None if hits else NO_EVIDENCE,
        limitations=[
            f"Scope: snapshot {snapshot.commit_sha[:12]}; sources searched: analyzed files and "
            "their declared symbols, README and docs, manifests, source excerpts, inferred "
            "modules, hotspots, and up to 500 recent commits.",
            "Semantic matches come from LSA embeddings and are inferred, not exact.",
        ],
    )
