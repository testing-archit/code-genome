import hashlib
import hmac
import json
import logging
import re
from typing import Annotated, Any, Literal

from code_genome_git import validate_ref
from fastapi import APIRouter, BackgroundTasks, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..ids import new_id
from ..models import AnalysisRun, Repository, WebhookDelivery
from ..queue import enqueue_analysis
from ..schemas import (
    RepositoryAutomationPut,
    RepositoryAutomationResponse,
    WebhookResultResponse,
)
from .intelligence import _repository

router = APIRouter(tags=["automation"])
logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/github"
WEBHOOK_ACTOR = "system:github-webhook"
_DELIVERY_ID = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_EVENT = re.compile(r"^[a-z_]{1,64}$")
WebhookOutcome = Literal["queued", "coalesced", "ignored", "duplicate", "pong"]
_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _automation(repository: Repository) -> RepositoryAutomationResponse:
    secret = get_settings().github_webhook_secret
    return RepositoryAutomationResponse(
        repository_id=repository.id,
        auto_analyze=repository.auto_analyze,
        branch=repository.default_branch,
        webhook_configured=bool(secret and secret.get_secret_value()),
        webhook_path=f"/api/v1{WEBHOOK_PATH}",
        events=["push"],
    )


@router.get("/repositories/{repository_id}/automation", response_model=RepositoryAutomationResponse)
def get_automation(repository_id: str, db: Database, actor: Actor) -> RepositoryAutomationResponse:
    return _automation(_repository(db, repository_id, actor))


@router.put("/repositories/{repository_id}/automation", response_model=RepositoryAutomationResponse)
def put_automation(
    repository_id: str,
    payload: RepositoryAutomationPut,
    request: Request,
    db: Database,
    actor: Actor,
) -> RepositoryAutomationResponse:
    repository = _repository(db, repository_id, actor)
    if actor.role not in {"owner", "admin"}:
        raise AppError(
            403,
            "FORBIDDEN",
            "Insufficient repository permissions",
            "Only workspace owners and admins can change automatic analysis.",
        )
    if repository.auto_analyze != payload.auto_analyze:
        repository.auto_analyze = payload.auto_analyze
        record_audit_event(
            db,
            workspace_id=actor.workspace_id,
            actor_id=actor.user_id,
            action=(
                "repository.automation.enabled"
                if payload.auto_analyze
                else "repository.automation.disabled"
            ),
            resource_type="repository",
            resource_id=repository.id,
            request_id=request.state.request_id,
        )
        db.commit()
    return _automation(repository)


def _verify_signature(body: bytes, signature: str | None) -> None:
    secret = get_settings().github_webhook_secret
    if secret is None or not secret.get_secret_value():
        raise AppError(
            503,
            "WEBHOOK_NOT_CONFIGURED",
            "Webhooks unavailable",
            "CODE_GENOME_GITHUB_WEBHOOK_SECRET is not configured.",
        )
    expected = (
        "sha256=" + hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    )
    if not signature or not hmac.compare_digest(expected, signature):
        raise AppError(
            401, "INVALID_SIGNATURE", "Signature rejected", "X-Hub-Signature-256 is invalid."
        )


def _push_target(payload: Any) -> tuple[str, str] | None:
    """Return (lowercased full name, branch) for a branch push, or None to ignore it."""
    if not isinstance(payload, dict) or payload.get("deleted") is True:
        return None
    ref = payload.get("ref")
    repository = payload.get("repository")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        return None
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(full_name, str) or not _FULL_NAME.match(full_name):
        raise AppError(
            422, "INVALID_PAYLOAD", "Invalid payload", "repository.full_name is invalid."
        )
    try:
        branch = validate_ref(ref.removeprefix("refs/heads/"))
    except ValueError as error:
        raise AppError(422, "INVALID_PAYLOAD", "Invalid payload", "ref is invalid.") from error
    return full_name.lower(), branch


@router.post(WEBHOOK_PATH, response_model=WebhookResultResponse)
async def receive_github_webhook(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Database,
    x_github_event: Annotated[str, Header()],
    x_github_delivery: Annotated[str, Header()],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> WebhookResultResponse:
    """Queue re-analysis for opted-in repositories when their default branch is pushed.

    The payload only selects which repositories to refresh. The analysis itself fetches
    authoritative state from GitHub, so payload commit claims are never stored as facts.
    """
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)
    if not _DELIVERY_ID.match(x_github_delivery) or not _EVENT.match(x_github_event):
        raise AppError(400, "INVALID_PAYLOAD", "Invalid headers", "GitHub headers are malformed.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppError(400, "INVALID_PAYLOAD", "Invalid payload", "Body is not JSON.") from error

    delivery = WebhookDelivery(
        id=new_id("whd"),
        provider="github",
        delivery_id=x_github_delivery,
        event=x_github_event,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        outcome="ignored",
        queued_runs=0,
    )
    db.add(delivery)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return WebhookResultResponse(
            delivery_id=x_github_delivery,
            event=x_github_event,
            outcome="duplicate",
            queued_run_ids=[],
            detail="This delivery was already processed.",
        )

    def finish(outcome: WebhookOutcome, detail: str, run_ids: list[str]) -> WebhookResultResponse:
        delivery.outcome = outcome
        delivery.queued_runs = len(run_ids)
        db.commit()
        logger.info(
            "github_webhook_processed",
            extra={
                "delivery_id": x_github_delivery,
                "github_event": x_github_event,
                "outcome": outcome,
                "queued_runs": len(run_ids),
            },
        )
        return WebhookResultResponse(
            delivery_id=x_github_delivery,
            event=x_github_event,
            outcome=outcome,
            queued_run_ids=run_ids,
            detail=detail,
        )

    if x_github_event == "ping":
        return finish("pong", "Webhook reached CODE GENOME.", [])
    if x_github_event != "push":
        return finish("ignored", f"Event '{x_github_event}' does not trigger analysis.", [])
    target = _push_target(payload)
    if target is None:
        return finish("ignored", "Only branch pushes that add commits trigger analysis.", [])
    full_name, branch = target

    repositories = list(
        db.scalars(
            select(Repository).where(
                Repository.provider == "github",
                Repository.external_id == full_name,
                Repository.default_branch == branch,
                Repository.auto_analyze.is_(True),
            )
        )
    )
    run_ids: list[str] = []
    coalesced = 0
    for repository in repositories:
        active = db.scalar(
            select(AnalysisRun.id).where(
                AnalysisRun.workspace_id == repository.workspace_id,
                AnalysisRun.repository_id == repository.id,
                AnalysisRun.state.in_(("QUEUED", "RUNNING")),
            )
        )
        if active is not None:
            coalesced += 1
            continue
        run = AnalysisRun(
            id=new_id("run"),
            workspace_id=repository.workspace_id,
            repository_id=repository.id,
            requested_refs=[branch],
            state="QUEUED",
            progress=0,
            version="structural-genome@0.1.0",
        )
        db.add(run)
        record_audit_event(
            db,
            workspace_id=repository.workspace_id,
            actor_id=WEBHOOK_ACTOR,
            action="repository.analysis.queued",
            resource_type="analysis_run",
            resource_id=run.id,
            after_hash=delivery.payload_sha256,
            request_id=request.state.request_id,
        )
        run_ids.append(run.id)

    if not repositories:
        return finish("ignored", "No repository has automatic analysis on for this branch.", [])
    result = finish(
        "queued" if run_ids else "coalesced",
        f"Queued {len(run_ids)} analysis run(s); {coalesced} already in progress.",
        run_ids,
    )
    for run_id in run_ids:
        await enqueue_analysis(run_id, background_tasks)
    response.status_code = status.HTTP_202_ACCEPTED if run_ids else status.HTTP_200_OK
    return result
