import json
import logging
from collections.abc import Iterator
from datetime import timedelta

from code_genome_ml import RETRIEVAL_VERSION
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..ids import new_id
from ..models import ChatConversation, ChatMessage, GroundedAnswer, RepositorySnapshot, utc_now
from ..schemas import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    ConversationTurnResponse,
)
from ..services.gemini import (
    ConversationTurn,
    GeminiProviderError,
    finalize_streamed_answer,
    stream_grounded_answer,
)
from ..services.grounding import (
    plan_grounded_answer,
    produce_grounded_answer,
    record_grounded_answer,
)
from .intelligence import _repository, _snapshot, grounded_answer_response

router = APIRouter(tags=["conversations"])
logger = logging.getLogger(__name__)
MAX_MESSAGES_PER_CONVERSATION = 400


def _conversation(db: Database, conversation_id: str, actor: Actor) -> ChatConversation:
    # Conversations are private to their author inside the workspace.
    conversation = db.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.workspace_id == actor.workspace_id,
            ChatConversation.created_by == actor.user_id,
        )
    )
    if conversation is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Conversation was not found.")
    return conversation


def _message_count(db: Database, conversation_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
        )
        or 0
    )


def _summary(db: Database, conversation: ChatConversation) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        id=conversation.id,
        repository_id=conversation.repository_id,
        title=conversation.title,
        message_count=_message_count(db, conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(
    message: ChatMessage, answers: dict[str, GroundedAnswer]
) -> ConversationMessageResponse:
    answer = answers.get(message.answer_id) if message.answer_id else None
    return ConversationMessageResponse(
        id=message.id,
        role="assistant" if message.role == "assistant" else "user",
        channel=message.channel,
        content=message.content,
        created_at=message.created_at,
        answer=grounded_answer_response(answer) if answer else None,
    )


@router.get(
    "/repositories/{repository_id}/conversations",
    response_model=list[ConversationSummaryResponse],
)
def list_conversations(
    repository_id: str,
    db: Database,
    actor: Actor,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationSummaryResponse]:
    _repository(db, repository_id, actor)
    conversations = db.scalars(
        select(ChatConversation)
        .where(
            ChatConversation.workspace_id == actor.workspace_id,
            ChatConversation.repository_id == repository_id,
            ChatConversation.created_by == actor.user_id,
        )
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
    )
    return [_summary(db, conversation) for conversation in conversations]


@router.post(
    "/repositories/{repository_id}/conversations",
    response_model=ConversationSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    repository_id: str,
    payload: ConversationCreate,
    request: Request,
    db: Database,
    actor: Actor,
) -> ConversationSummaryResponse:
    _repository(db, repository_id, actor)
    conversation = ChatConversation(
        id=new_id("cnv"),
        workspace_id=actor.workspace_id,
        repository_id=repository_id,
        title=(payload.title or "New conversation").strip(),
        created_by=actor.user_id,
    )
    db.add(conversation)
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="conversation.created",
        resource_type="conversation",
        resource_id=conversation.id,
        request_id=request.state.request_id,
    )
    db.commit()
    return _summary(db, conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str, db: Database, actor: Actor) -> ConversationResponse:
    conversation = _conversation(db, conversation_id, actor)
    messages = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    )
    answer_ids = [message.answer_id for message in messages if message.answer_id]
    answers = {
        answer.id: answer
        for answer in db.scalars(
            select(GroundedAnswer).where(
                GroundedAnswer.id.in_(answer_ids),
                GroundedAnswer.workspace_id == actor.workspace_id,
            )
        )
    }
    summary = _summary(db, conversation)
    return ConversationResponse(
        **summary.model_dump(),
        messages=[_message_response(message, answers) for message in messages],
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation_message(
    conversation_id: str,
    payload: ConversationMessageCreate,
    request: Request,
    db: Database,
    actor: Actor,
) -> ConversationTurnResponse:
    conversation = _conversation(db, conversation_id, actor)
    _repository(db, conversation.repository_id, actor)
    snapshot = _snapshot(db, conversation.repository_id, actor)
    if _message_count(db, conversation.id) >= MAX_MESSAGES_PER_CONVERSATION:
        raise AppError(
            409,
            "CONVERSATION_FULL",
            "Conversation is full",
            "Start a new conversation to continue asking questions.",
        )
    previous = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(6)
        )
    )
    history = tuple(ConversationTurn(item.role, item.content) for item in reversed(previous))
    question = payload.content.strip()
    user_message = ChatMessage(
        id=new_id("msg"),
        workspace_id=actor.workspace_id,
        conversation_id=conversation.id,
        role="user",
        channel=payload.channel,
        content=question,
    )
    db.add(user_message)
    answer = produce_grounded_answer(
        db,
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        repository_id=conversation.repository_id,
        snapshot=snapshot,
        question=question,
        request_id=request.state.request_id,
        history=history,
        channel=payload.channel,
        language=payload.language,
    )
    assistant_message = ChatMessage(
        id=new_id("msg"),
        workspace_id=actor.workspace_id,
        conversation_id=conversation.id,
        role="assistant",
        channel=payload.channel,
        content=answer.answer,
        answer_id=answer.id,
    )
    db.add(assistant_message)
    if not previous and conversation.title == "New conversation":
        conversation.title = question[:80] + ("…" if len(question) > 80 else "")
    conversation.updated_at = utc_now()
    db.commit()
    db.refresh(answer)
    answers = {answer.id: answer}
    return ConversationTurnResponse(
        conversation=_summary(db, conversation),
        user_message=_message_response(user_message, answers),
        assistant_message=_message_response(assistant_message, answers),
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str, request: Request, db: Database, actor: Actor
) -> Response:
    conversation = _conversation(db, conversation_id, actor)
    # Grounded answers stay: they are the audited record of what was said and cited.
    for message in db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
    ):
        db.delete(message)
    db.delete(conversation)
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="conversation.deleted",
        resource_type="conversation",
        resource_id=conversation_id,
        request_id=request.state.request_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sse(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@router.post("/conversations/{conversation_id}/messages/stream")
def stream_conversation_message(
    conversation_id: str,
    payload: ConversationMessageCreate,
    request: Request,
    db: Database,
    actor: Actor,
) -> StreamingResponse:
    """Answer a follow-up as server-sent events while Gemini writes it.

    Events: ``status`` (stage), ``delta`` (draft text), ``fallback`` (the draft failed
    citation checks and is replaced), ``done`` (the persisted ConversationTurn), and
    ``error``. Draft text is unverified; only the ``done`` answer is authoritative. Nothing
    is stored unless the stream completes.
    """
    conversation = _conversation(db, conversation_id, actor)
    _repository(db, conversation.repository_id, actor)
    snapshot = _snapshot(db, conversation.repository_id, actor)
    if _message_count(db, conversation.id) >= MAX_MESSAGES_PER_CONVERSATION:
        raise AppError(
            409,
            "CONVERSATION_FULL",
            "Conversation is full",
            "Start a new conversation to continue asking questions.",
        )
    previous = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(6)
        )
    )
    history = tuple(ConversationTurn(item.role, item.content) for item in reversed(previous))
    question = payload.content.strip()
    plan = plan_grounded_answer(
        db,
        repository_id=conversation.repository_id,
        snapshot=snapshot,
        question=question,
        history=history,
    )
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    bind = db.get_bind()
    request_id = request.state.request_id
    workspace_id, user_id = actor.workspace_id, actor.user_id
    conversation_ref, snapshot_ref = conversation.id, snapshot.id
    first_turn = not previous

    def events() -> Iterator[bytes]:
        result = plan.extractive
        retrieval_version = RETRIEVAL_VERSION
        if plan.can_phrase:
            yield _sse("status", {"stage": "writing", "model": settings.gemini_model})
            draft: list[str] = []
            try:
                for piece in stream_grounded_answer(
                    api_key=api_key,
                    model=settings.gemini_model,
                    question=question,
                    documents=plan.documents,
                    timeout_seconds=settings.gemini_timeout_seconds,
                    max_output_tokens=settings.gemini_max_output_tokens,
                    history=history,
                    language=payload.language,
                ):
                    draft.append(piece)
                    yield _sse("delta", {"text": piece})
                result = finalize_streamed_answer("".join(draft), plan.documents)
                retrieval_version = f"{RETRIEVAL_VERSION}+gemini:{settings.gemini_model}"
                if not result.evidence_ids and plan.routed:
                    result, retrieval_version = plan.extractive, RETRIEVAL_VERSION
                    yield _sse("fallback", {"reason": "Showing the deterministic answer."})
            except GeminiProviderError as error:
                logger.warning(
                    "gemini_stream_fallback",
                    extra={"conversation_id": conversation_ref, "reason": str(error)},
                )
                result = plan.extractive
                if draft:
                    yield _sse(
                        "fallback",
                        {"reason": "The draft could not be verified against its citations."},
                    )
        try:
            with Session(bind, expire_on_commit=False) as session:
                stored_snapshot = session.get(RepositorySnapshot, snapshot_ref)
                stored_conversation = session.get(ChatConversation, conversation_ref)
                if stored_snapshot is None or stored_conversation is None:
                    yield _sse("error", {"code": "NOT_FOUND", "detail": "Conversation is gone."})
                    return
                user_message = ChatMessage(
                    id=new_id("msg"),
                    workspace_id=workspace_id,
                    conversation_id=conversation_ref,
                    role="user",
                    channel=payload.channel,
                    content=question,
                )
                session.add(user_message)
                answer = record_grounded_answer(
                    session,
                    plan=plan,
                    result=result,
                    retrieval_version=retrieval_version,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    repository_id=stored_conversation.repository_id,
                    snapshot=stored_snapshot,
                    question=question,
                    request_id=request_id,
                    channel=payload.channel,
                    language=payload.language,
                )
                assistant_message = ChatMessage(
                    id=new_id("msg"),
                    workspace_id=workspace_id,
                    conversation_id=conversation_ref,
                    role="assistant",
                    channel=payload.channel,
                    content=answer.answer,
                    answer_id=answer.id,
                )
                session.add(assistant_message)
                if first_turn and stored_conversation.title == "New conversation":
                    stored_conversation.title = question[:80] + ("…" if len(question) > 80 else "")
                stored_conversation.updated_at = utc_now()
                # Messages are listed by creation time, so the answer must sort after
                # the question even when both are written in the same instant.
                user_message.created_at = stored_conversation.updated_at
                assistant_message.created_at = stored_conversation.updated_at + timedelta(
                    microseconds=1
                )
                session.commit()
                answers = {answer.id: answer}
                turn = ConversationTurnResponse(
                    conversation=_summary(session, stored_conversation),
                    user_message=_message_response(user_message, answers),
                    assistant_message=_message_response(assistant_message, answers),
                )
            yield _sse("done", turn.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - the stream must end with an event, not a reset
            logger.exception(
                "conversation_stream_failed", extra={"conversation_id": conversation_ref}
            )
            yield _sse(
                "error", {"code": "STREAM_FAILED", "detail": "The answer could not be saved."}
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
