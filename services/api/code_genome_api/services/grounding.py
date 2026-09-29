import logging
import re
from collections.abc import Sequence

from code_genome_intelligence import GroundedResult, RetrievalDocument, answer_question
from code_genome_ml import RETRIEVAL_VERSION
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit_event
from ..config import get_settings
from ..ids import new_id
from ..models import (
    FileHotspot,
    GroundedAnswer,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)
from . import ml
from .gemini import (
    ConversationTurn,
    GeminiProviderError,
    english_search_query,
    generate_grounded_answer,
)

logger = logging.getLogger(__name__)
LEXICAL_VERSION = "lexical-grounding@0.1.0"
RECENCY_TERMS = {"latest", "newest", "recent", "recently", "haal", "abhi"}


def _hybrid_select(
    db: Session,
    snapshot: RepositorySnapshot,
    query: str,
    pool: dict[str, RetrievalDocument],
    limit: int = 5,
) -> GroundedResult:
    """Rank evidence with the snapshot's hybrid retriever (BM25 + LSA + rank fusion)."""
    mode = ml.retrieval_mode(db, snapshot)
    hits = ml.retriever(db, snapshot).search(query, limit=limit, mode=mode)
    if not hits:
        return answer_question(query, tuple(pool.values()))
    for hit in hits:
        document = hit.document
        if document.id not in pool:
            text = document.text
            if document.kind == "file":
                declared = document.text.split(" ", 2)[-1].strip()
                text = (
                    f"File {document.path} declares {declared[:400]}."
                    if declared
                    else f"File {document.path} is an analyzed source file."
                )
            pool[document.id] = RetrievalDocument(document.id, document.kind, text)
    selected = [pool[hit.document.id] for hit in hits]
    facts = "; ".join(item.text[:220].strip() for item in selected)
    semantic_only = [hit for hit in hits if hit.bm25_rank is None]
    limitations = [
        "This answer is extractive and limited to the latest published repository evidence.",
        f"Evidence was ranked by {mode} retrieval, the mode that scored best on this "
        "repository's retrieval evaluation.",
    ]
    if semantic_only:
        limitations.append(
            f"{len(semantic_only)} cited item(s) matched by semantic similarity only (inferred)."
        )
    return GroundedResult(
        f"Relevant repository evidence: {facts}",
        tuple(item.id for item in selected),
        tuple(limitations),
    )


def _select(
    db: Session, snapshot: RepositorySnapshot, query: str, pool: dict[str, RetrievalDocument]
) -> GroundedResult:
    terms = set(re.findall(r"[a-z]+", query.lower()))
    if terms & RECENCY_TERMS:
        # "What changed recently?" is answered from commit order, not similarity.
        recent = answer_question(query, tuple(pool.values()))
        if recent.evidence_ids:
            return recent
    try:
        return _hybrid_select(db, snapshot, query, pool)
    except ValueError:
        logger.warning("hybrid_retrieval_failed", extra={"snapshot_id": snapshot.id})
        return answer_question(query, tuple(pool.values()))


def retrieval_documents(
    db: Session, repository_id: str, snapshot: RepositorySnapshot
) -> tuple[RetrievalDocument, ...]:
    documents: list[RetrievalDocument] = []
    for module in db.scalars(
        select(ModuleCandidate).where(ModuleCandidate.snapshot_id == snapshot.id)
    ):
        documents.append(
            RetrievalDocument(
                f"module:{module.id}",
                "module",
                f"Module {module.natural_key}: {module.description}",
            )
        )
    for hotspot in db.scalars(select(FileHotspot).where(FileHotspot.snapshot_id == snapshot.id)):
        documents.append(
            RetrievalDocument(
                f"hotspot:{hotspot.path}",
                "hotspot",
                f"File {hotspot.path} is a relative hotspot with {hotspot.commit_count} commits.",
            )
        )
    for commit in db.scalars(
        select(RepositoryCommit)
        .where(
            RepositoryCommit.repository_id == repository_id,
            RepositoryCommit.workspace_id == snapshot.workspace_id,
        )
        .order_by(RepositoryCommit.authored_at.desc())
        .limit(200)
    ):
        documents.append(RetrievalDocument(f"commit:{commit.sha}", "commit", commit.message))
    return tuple(documents)


def produce_grounded_answer(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    repository_id: str,
    snapshot: RepositorySnapshot,
    question: str,
    request_id: str,
    history: Sequence[ConversationTurn] = (),
    channel: str = "text",
    language: str = "auto",
) -> GroundedAnswer:
    """Retrieve evidence, optionally let Gemini phrase it, and persist an audited answer.

    ``history`` only helps resolve follow-up references; it is never treated as evidence.
    The caller commits the session.
    """
    pool = {document.id: document for document in retrieval_documents(db, repository_id, snapshot)}
    # Follow-ups such as "and who changed it?" carry few terms, so retrieval also
    # considers the previous user question. Evidence selection never uses a model call.
    retrieval_query = question
    previous_user = next((turn.text for turn in reversed(history) if turn.role == "user"), None)
    result = _select(db, snapshot, question, pool)
    if not result.evidence_ids and previous_user:
        retrieval_query = f"{previous_user}\n{question}"
        result = _select(db, snapshot, retrieval_query, pool)
    retrieval_version = RETRIEVAL_VERSION
    settings = get_settings()
    api_key = settings.gemini_api_key
    gemini_enabled = api_key is not None and bool(api_key.get_secret_value())
    rewritten_query: str | None = None
    if not result.evidence_ids and gemini_enabled and api_key is not None:
        # Hindi (Devanagari) or Hinglish questions rarely share tokens with English
        # evidence. Gemini only proposes English keywords; selection stays lexical.
        try:
            rewritten_query = english_search_query(
                api_key=api_key.get_secret_value(),
                model=settings.gemini_model,
                question=retrieval_query,
                timeout_seconds=settings.gemini_timeout_seconds,
            )
            result = _select(db, snapshot, rewritten_query, pool)
        except GeminiProviderError:
            logger.warning(
                "gemini_query_rewrite_failed",
                extra={"repository_id": repository_id, "snapshot_id": snapshot.id},
            )
    if result.evidence_ids and gemini_enabled and api_key is not None:
        selected = tuple(pool[item] for item in result.evidence_ids if item in pool)
        try:
            result = generate_grounded_answer(
                api_key=api_key.get_secret_value(),
                model=settings.gemini_model,
                question=question,
                documents=selected,
                timeout_seconds=settings.gemini_timeout_seconds,
                max_output_tokens=settings.gemini_max_output_tokens,
                history=tuple(history),
                language=language,
            )
            retrieval_version = f"{RETRIEVAL_VERSION}+gemini:{settings.gemini_model}"
        except GeminiProviderError:
            logger.warning(
                "gemini_grounded_answer_fallback",
                extra={"repository_id": repository_id, "snapshot_id": snapshot.id},
            )
    answer = GroundedAnswer(
        id=new_id("ans"),
        workspace_id=workspace_id,
        repository_id=repository_id,
        question=question,
        answer=result.answer,
        evidence_ids=list(result.evidence_ids),
        scope_json={
            "snapshot_id": snapshot.id,
            "snapshot_sha": snapshot.commit_sha,
            "analysis_version": snapshot.analysis_version,
            "channel": channel,
            "language": language,
            **({"search_query": rewritten_query} if rewritten_query else {}),
        },
        limitations=list(result.limitations)
        + (
            ["Evidence was selected with a model-translated English search query (inferred)."]
            if rewritten_query and result.evidence_ids
            else []
        ),
        retrieval_version=retrieval_version,
        created_by=user_id,
    )
    db.add(answer)
    record_audit_event(
        db,
        workspace_id=workspace_id,
        actor_id=user_id,
        action="grounded_answer.created",
        resource_type="grounded_answer",
        resource_id=answer.id,
        request_id=request_id,
    )
    return answer
