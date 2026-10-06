"""The workspace-level Code Genome assistant: product documentation and every repository.

Typed questions about Code Genome itself are answered from its own documentation with section
citations. Voice sessions get one locked Gemini Live setup with three tools: documentation search,
a per-repository evidence question (proxied by the browser to the audited answer endpoint), and
opening an app page.
"""

import logging

from code_genome_intelligence import GroundedResult, answer_question
from fastapi import APIRouter, Request, status
from sqlalchemy import select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..ids import new_id
from ..models import Repository, RepositorySnapshot
from ..schemas import (
    AssistantAnswerCreate,
    AssistantAnswerResponse,
    AssistantSourceResponse,
    AssistantVoiceSessionCreate,
    VoiceSessionResponse,
)
from ..services import product_knowledge
from ..services.gemini import GeminiProviderError, generate_grounded_answer
from ..services.gemini_live import (
    CONSTRAINED_WEBSOCKET_URL,
    REFUSAL,
    assistant_setup,
    create_live_session,
)

router = APIRouter(tags=["assistant"])
logger = logging.getLogger(__name__)


def _api_key() -> str | None:
    key = get_settings().gemini_api_key
    return key.get_secret_value() if key is not None and key.get_secret_value() else None


@router.post("/assistant/answers", response_model=AssistantAnswerResponse)
def ask_assistant(
    payload: AssistantAnswerCreate, request: Request, db: Database, actor: Actor
) -> AssistantAnswerResponse:
    """Answer a question about Code Genome itself from its documentation, with citations."""
    sections = product_knowledge.search(payload.question)
    documents = product_knowledge.documents(tuple(sections))
    limitations = [
        "Answered from Code Genome's own documentation; repository facts come from each "
        "repository's analysis, not from here."
    ]
    result: GroundedResult
    key = _api_key()
    if not documents:
        result = GroundedResult(REFUSAL, (), tuple(limitations))
    elif key is not None:
        settings = get_settings()
        try:
            result = generate_grounded_answer(
                api_key=key,
                model=settings.gemini_model,
                question=payload.question,
                documents=documents,
                timeout_seconds=settings.gemini_timeout_seconds,
                max_output_tokens=settings.gemini_max_output_tokens,
                language=payload.language,
            )
        except GeminiProviderError:
            result = answer_question(payload.question, documents)
            limitations.append("The model was unavailable; showing extracted documentation.")
    else:
        result = answer_question(payload.question, documents)
    cited = set(result.evidence_ids)
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="assistant_answer.created",
        resource_type="workspace",
        resource_id=actor.workspace_id,
        request_id=request.state.request_id,
    )
    db.commit()
    return AssistantAnswerResponse(
        answer=result.answer,
        evidence_ids=list(result.evidence_ids),
        limitations=[*result.limitations, *limitations],
        sources=[
            AssistantSourceResponse(
                id=section.id, path=section.path, title=section.title, excerpt=section.text[:600]
            )
            for section in sections
            if section.id in cited
        ],
        knowledge_version=product_knowledge.PRODUCT_KNOWLEDGE_VERSION,
    )


def workspace_brief(db: Database, workspace_id: str) -> str:
    """One line per repository: name, branch, and whether it has a published snapshot."""
    lines: list[str] = []
    for repository in db.scalars(
        select(Repository)
        .where(Repository.workspace_id == workspace_id)
        .order_by(Repository.external_id)
        .limit(50)
    ):
        snapshot = db.scalar(
            select(RepositorySnapshot.commit_sha)
            .where(
                RepositorySnapshot.repository_id == repository.id,
                RepositorySnapshot.workspace_id == workspace_id,
                RepositorySnapshot.published_at.is_not(None),
                RepositorySnapshot.as_of.is_(None),
            )
            .order_by(RepositorySnapshot.published_at.desc())
            .limit(1)
        )
        state = f"analysed at {snapshot[:8]}" if snapshot else "not analysed yet"
        lines.append(f"{repository.external_id} (branch {repository.default_branch}, {state})")
    return "; ".join(lines)


@router.post(
    "/voice/assistant-sessions",
    response_model=VoiceSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assistant_session(
    payload: AssistantVoiceSessionCreate, request: Request, db: Database, actor: Actor
) -> VoiceSessionResponse:
    key = _api_key()
    if key is None:
        raise AppError(
            503,
            "VOICE_UNAVAILABLE",
            "Voice assistant unavailable",
            "Set GEMINI_API_KEY on the API server to enable the voice assistant.",
        )
    settings = get_settings()
    setup = assistant_setup(
        model=settings.gemini_live_model,
        voice=payload.voice,
        language=payload.language,
        workspace_brief=workspace_brief(db, actor.workspace_id),
    )
    try:
        session = create_live_session(
            api_key=key,
            model=settings.gemini_live_model,
            voice=payload.voice,
            repository_name="",
            snapshot_sha="",
            timeout_seconds=settings.gemini_timeout_seconds,
            ttl_minutes=settings.gemini_live_session_minutes,
            language=payload.language,
            setup=setup,
        )
    except GeminiProviderError as error:
        logger.warning("assistant_live_token_failed", extra={"reason": str(error)})
        raise AppError(
            502,
            "VOICE_PROVIDER_ERROR",
            "Voice assistant unavailable",
            "The voice provider did not issue a session. Try again shortly.",
        ) from error
    session_id = new_id("vcs")
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="assistant_voice_session.created",
        resource_type="workspace",
        resource_id=actor.workspace_id,
        request_id=request.state.request_id,
        after_hash=session_id,
    )
    db.commit()
    return VoiceSessionResponse(
        id=session_id,
        repository_id=None,
        snapshot_sha=None,
        model=settings.gemini_live_model,
        voice=payload.voice,
        language=payload.language,
        websocket_url=CONSTRAINED_WEBSOCKET_URL,
        token=session.token,
        expires_at=session.expires_at,
        new_session_expires_at=session.new_session_expires_at,
        setup=session.setup,
        limitations=[
            "Product answers come from Code Genome's documentation; repository answers come "
            "from each repository's cited evidence.",
            "Speech recognition can mishear names; transcripts are best-effort.",
        ],
    )
