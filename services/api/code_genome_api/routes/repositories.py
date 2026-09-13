from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, Response, status
from sqlalchemy import select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..idempotency import find_idempotent_resource, fingerprint, record_idempotency
from ..ids import new_id
from ..models import (
    BranchRef,
    FileManifestEntry,
    Repository,
    RepositoryCommit,
    RepositoryConnection,
    RepositorySnapshot,
    utc_now,
)
from ..queue import enqueue_mirror_deletion
from ..schemas import (
    BranchRefResponse,
    CommitResponse,
    FileManifestResponse,
    RepositoryConnectionPut,
    RepositoryConnectionResponse,
    RepositoryCreate,
    RepositoryInventoryResponse,
    RepositoryResponse,
)
from ..services.credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    public_metadata_hash,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _external_id(clone_url: str) -> str:
    return clone_url.removeprefix("https://github.com/").removesuffix(".git").lower()


def _repository_for_actor(db: Database, repository_id: str, actor: Actor) -> Repository:
    repository = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == actor.workspace_id
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")
    return repository


def _require_connection_admin(actor: Actor) -> None:
    if actor.role not in {"owner", "admin"}:
        raise AppError(
            403,
            "FORBIDDEN",
            "Insufficient repository permissions",
            "Only workspace owners and admins can manage repository credentials.",
        )


def _connection_response(connection: RepositoryConnection | None) -> RepositoryConnectionResponse:
    if connection is None:
        return RepositoryConnectionResponse(
            connected=False,
            connection_id=None,
            provider="github",
            token_kind=None,
            scopes=[],
            key_version=None,
            installed_at=None,
            revoked_at=None,
        )
    return RepositoryConnectionResponse(
        connected=connection.revoked_at is None,
        connection_id=connection.id,
        provider=connection.provider,
        token_kind=connection.token_kind if connection.revoked_at is None else None,
        scopes=connection.scopes if connection.revoked_at is None else [],
        key_version=connection.key_version if connection.revoked_at is None else None,
        installed_at=connection.installed_at,
        revoked_at=connection.revoked_at,
    )


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


@router.get("/{repository_id}/connection", response_model=RepositoryConnectionResponse)
def get_repository_connection(
    repository_id: str, db: Database, actor: Actor
) -> RepositoryConnectionResponse:
    _repository_for_actor(db, repository_id, actor)
    connection = db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.repository_id == repository_id,
            RepositoryConnection.workspace_id == actor.workspace_id,
        )
    )
    return _connection_response(connection)


@router.put("/{repository_id}/connection", response_model=RepositoryConnectionResponse)
def put_repository_connection(
    repository_id: str,
    payload: RepositoryConnectionPut,
    request: Request,
    db: Database,
    actor: Actor,
) -> RepositoryConnectionResponse:
    _repository_for_actor(db, repository_id, actor)
    _require_connection_admin(actor)
    settings = get_settings()
    key = settings.credential_encryption_key
    if key is None or not key.get_secret_value():
        raise AppError(
            503,
            "CREDENTIAL_STORE_UNAVAILABLE",
            "Credential store unavailable",
            "Repository credential encryption is not configured.",
        )
    token_kind = payload.token_kind.value
    if "contents:read" not in payload.scopes:
        raise AppError(
            422,
            "INSUFFICIENT_PROVIDER_SCOPE",
            "Read scope required",
            "Private repository connections must declare contents:read.",
        )
    try:
        cipher = CredentialCipher(key.get_secret_value())
    except CredentialConfigurationError as error:
        raise AppError(
            503,
            "CREDENTIAL_STORE_UNAVAILABLE",
            "Credential store unavailable",
            "Repository credential encryption is not configured correctly.",
        ) from error
    envelope = cipher.encrypt(payload.token.get_secret_value(), actor.workspace_id, repository_id)
    connection = db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.repository_id == repository_id,
            RepositoryConnection.workspace_id == actor.workspace_id,
        )
    )
    before_hash = (
        public_metadata_hash(
            connection.token_kind, connection.scopes, connection.revoked_at is None
        )
        if connection
        else None
    )
    if connection is None:
        connection = RepositoryConnection(
            id=new_id("con"),
            workspace_id=actor.workspace_id,
            repository_id=repository_id,
            provider="github",
            token_kind=token_kind,
            credential_ciphertext=envelope.ciphertext,
            credential_nonce=envelope.nonce,
            key_version=settings.credential_key_version,
            scopes=payload.scopes,
            created_by=actor.user_id,
        )
        db.add(connection)
    else:
        connection.token_kind = token_kind
        connection.credential_ciphertext = envelope.ciphertext
        connection.credential_nonce = envelope.nonce
        connection.key_version = settings.credential_key_version
        connection.scopes = payload.scopes
        connection.created_by = actor.user_id
        connection.installed_at = utc_now()
        connection.revoked_at = None
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="repository.connection.upserted",
        resource_type="repository_connection",
        resource_id=connection.id,
        before_hash=before_hash,
        after_hash=public_metadata_hash(token_kind, payload.scopes, True),
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(connection)
    return _connection_response(connection)


@router.delete("/{repository_id}/connection", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_repository_connection(
    repository_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Database,
    actor: Actor,
) -> Response:
    _repository_for_actor(db, repository_id, actor)
    _require_connection_admin(actor)
    connection = db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.repository_id == repository_id,
            RepositoryConnection.workspace_id == actor.workspace_id,
            RepositoryConnection.revoked_at.is_(None),
        )
    )
    if connection is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Connection was not found.")
    before_hash = public_metadata_hash(connection.token_kind, connection.scopes, True)
    connection.credential_ciphertext = ""
    connection.credential_nonce = ""
    connection.revoked_at = utc_now()
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="repository.connection.revoked",
        resource_type="repository_connection",
        resource_id=connection.id,
        before_hash=before_hash,
        after_hash=public_metadata_hash(connection.token_kind, [], False),
        request_id=request.state.request_id,
    )
    db.commit()
    await enqueue_mirror_deletion(actor.workspace_id, repository_id, background_tasks)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{repository_id}/inventory", response_model=RepositoryInventoryResponse)
def get_repository_inventory(
    repository_id: str,
    db: Database,
    actor: Actor,
    commit_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    file_limit: Annotated[int, Query(ge=1, le=2_000)] = 500,
) -> RepositoryInventoryResponse:
    _repository_for_actor(db, repository_id, actor)
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
    refs = list(
        db.scalars(
            select(BranchRef)
            .where(
                BranchRef.repository_id == repository_id,
                BranchRef.workspace_id == actor.workspace_id,
            )
            .order_by(BranchRef.name)
        )
    )
    commits = list(
        db.scalars(
            select(RepositoryCommit)
            .where(
                RepositoryCommit.repository_id == repository_id,
                RepositoryCommit.workspace_id == actor.workspace_id,
            )
            .order_by(RepositoryCommit.authored_at.desc())
            .limit(commit_limit + 1)
        )
    )
    files = (
        list(
            db.scalars(
                select(FileManifestEntry)
                .where(
                    FileManifestEntry.snapshot_id == snapshot.id,
                    FileManifestEntry.workspace_id == actor.workspace_id,
                )
                .order_by(FileManifestEntry.path)
                .limit(file_limit + 1)
            )
        )
        if snapshot
        else []
    )
    limitations: list[str] = []
    if len(commits) > commit_limit:
        limitations.append(f"Commit history is limited to {commit_limit} records.")
    if len(files) > file_limit:
        limitations.append(f"File manifest is limited to {file_limit} records.")
    return RepositoryInventoryResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha if snapshot else None,
        refs=[
            BranchRefResponse(name=item.name, head_sha=item.head_sha, observed_at=item.observed_at)
            for item in refs
        ],
        commits=[
            CommitResponse(
                sha=item.sha,
                parent_shas=item.parent_shas,
                author_name=item.author_name,
                authored_at=item.authored_at,
                message=item.message,
            )
            for item in commits[:commit_limit]
        ],
        files=[
            FileManifestResponse(
                path=item.path,
                blob_sha=item.blob_sha,
                mode=item.mode,
                size=item.size,
                analyzed=item.analyzed,
            )
            for item in files[:file_limit]
        ],
        limitations=limitations,
    )
