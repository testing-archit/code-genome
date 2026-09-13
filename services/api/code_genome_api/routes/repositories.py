from typing import Annotated

from fastapi import APIRouter, Header, status
from sqlalchemy import select

from ..auth import Actor, Database
from ..errors import AppError
from ..idempotency import find_idempotent_resource, fingerprint, record_idempotency
from ..ids import new_id
from ..models import Repository
from ..schemas import RepositoryCreate, RepositoryResponse

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _external_id(clone_url: str) -> str:
    return clone_url.removeprefix("https://github.com/").removesuffix(".git").lower()


@router.get("", response_model=list[RepositoryResponse])
def list_repositories(db: Database, actor: Actor) -> list[Repository]:
    return list(
        db.scalars(
            select(Repository)
            .where(Repository.workspace_id == actor.workspace_id)
            .order_by(Repository.created_at.desc())
        )
    )


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
def create_repository(
    payload: RepositoryCreate,
    db: Database,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=160)],
) -> Repository:
    endpoint = "POST:/repositories"
    request_fingerprint = fingerprint(payload.model_dump(mode="json"))
    existing_id = find_idempotent_resource(
        db, actor.workspace_id, endpoint, idempotency_key, request_fingerprint
    )
    if existing_id:
        existing = db.scalar(
            select(Repository).where(
                Repository.id == existing_id, Repository.workspace_id == actor.workspace_id
            )
        )
        if existing:
            return existing

    external_id = _external_id(payload.clone_url)
    duplicate = db.scalar(
        select(Repository).where(
            Repository.workspace_id == actor.workspace_id,
            Repository.provider == "github",
            Repository.external_id == external_id,
        )
    )
    if duplicate:
        raise AppError(
            409,
            "REPOSITORY_EXISTS",
            "Repository already registered",
            "This GitHub repository is already registered in the workspace.",
        )

    repository = Repository(
        id=new_id("repo"),
        workspace_id=actor.workspace_id,
        provider="github",
        external_id=external_id,
        clone_url=payload.clone_url,
        default_branch=payload.default_branch,
    )
    db.add(repository)
    record_idempotency(
        db,
        actor.workspace_id,
        endpoint,
        idempotency_key,
        "repository",
        repository.id,
        request_fingerprint,
    )
    db.commit()
    db.refresh(repository)
    return repository
