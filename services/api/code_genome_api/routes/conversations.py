from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..errors import AppError
from ..ids import new_id
from ..models import ChatConversation, ChatMessage, GroundedAnswer, utc_now
from ..schemas import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    ConversationTurnResponse,
)
from ..services.gemini import ConversationTurn
from ..services.grounding import produce_grounded_answer
from .intelligence import _repository, _snapshot, grounded_answer_response

router = APIRouter(tags=["conversations"])
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
