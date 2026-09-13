from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, status
from sqlalchemy import select

from ..auth import Actor, Database
from ..errors import AppError
from ..idempotency import find_idempotent_resource, fingerprint, record_idempotency
from ..ids import new_id
from ..models import AnalysisRun, Repository
from ..queue import enqueue_analysis
from ..schemas import AnalysisCreate, AnalysisResponse

router = APIRouter(tags=["analyses"])


def _repository_for_actor(db: Database, repository_id: str, actor: Actor) -> Repository:
    repository = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == actor.workspace_id
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")
    return repository


@router.get("/repositories/{repository_id}/analyses", response_model=list[AnalysisResponse])
def list_analyses(repository_id: str, db: Database, actor: Actor) -> list[AnalysisRun]:
    _repository_for_actor(db, repository_id, actor)
    return list(
        db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.repository_id == repository_id,
                AnalysisRun.workspace_id == actor.workspace_id,
            )
            .order_by(AnalysisRun.created_at.desc())
        )
    )


@router.post(
    "/repositories/{repository_id}/analyses",
    response_model=AnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_analysis(
    repository_id: str,
    payload: AnalysisCreate,
    background_tasks: BackgroundTasks,
    db: Database,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=160)],
) -> AnalysisRun:
    repository = _repository_for_actor(db, repository_id, actor)
    refs = payload.refs or [repository.default_branch]
    endpoint = f"POST:/repositories/{repository_id}/analyses"
    request_fingerprint = fingerprint({"refs": refs, "simulate_failure": payload.simulate_failure})
    existing_id = find_idempotent_resource(
        db, actor.workspace_id, endpoint, idempotency_key, request_fingerprint
    )
    if existing_id:
        existing = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.id == existing_id,
                AnalysisRun.workspace_id == actor.workspace_id,
            )
        )
        if existing:
            return existing

    run = AnalysisRun(
        id=new_id("run"),
        workspace_id=actor.workspace_id,
        repository_id=repository_id,
        requested_refs=refs,
        state="QUEUED",
        progress=0,
        version="structural-genome@0.1.0",
        simulate_failure=payload.simulate_failure,
    )
    db.add(run)
    record_idempotency(
        db,
        actor.workspace_id,
        endpoint,
        idempotency_key,
        "analysis_run",
        run.id,
        request_fingerprint,
    )
    db.commit()
    db.refresh(run)
    await enqueue_analysis(run.id, background_tasks)
    return run


@router.get("/analyses/{run_id}", response_model=AnalysisResponse)
def get_analysis(run_id: str, db: Database, actor: Actor) -> AnalysisRun:
    run = db.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id, AnalysisRun.workspace_id == actor.workspace_id
        )
    )
    if run is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Analysis run was not found.")
    return run
