from code_genome_intelligence import (
    ImpactRelation,
    RetrievalDocument,
    RiskInput,
    answer_question,
    rank_impact,
    score_risks,
)
from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..errors import AppError
from ..ids import new_id
from ..models import (
    AnswerFeedback,
    CoChangeEdge,
    FileHotspot,
    GraphEdge,
    GraphNode,
    GroundedAnswer,
    ModuleCandidate,
    Repository,
    RepositoryCommit,
    RepositorySnapshot,
    RiskScore,
)
from ..schemas import (
    AnswerFeedbackCreate,
    AnswerFeedbackResponse,
    GroundedAnswerCreate,
    GroundedAnswerResponse,
    ImpactItemResponse,
    ImpactResponse,
    RiskResponse,
    RiskScoreResponse,
)

router = APIRouter(tags=["intelligence"])


def _repository(db: Database, repository_id: str, actor: Actor) -> Repository:
    repository = db.scalar(
        select(Repository).where(
            Repository.id == repository_id,
            Repository.workspace_id == actor.workspace_id,
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")
    return repository


def _snapshot(db: Database, repository_id: str, actor: Actor) -> RepositorySnapshot:
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
        raise AppError(404, "NOT_FOUND", "Evidence not found", "No published snapshot exists.")
    return snapshot


@router.get("/repositories/{repository_id}/risk", response_model=RiskResponse)
def get_risk(repository_id: str, db: Database, actor: Actor) -> RiskResponse:
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    stored = list(
        db.scalars(
            select(RiskScore)
            .where(RiskScore.snapshot_id == snapshot.id)
            .order_by(RiskScore.score.desc())
        )
    )
    if not stored:
        hotspots = list(
            db.scalars(select(FileHotspot).where(FileHotspot.snapshot_id == snapshot.id))
        )
        results = score_risks(
            tuple(
                RiskInput(
                    hotspot.path,
                    hotspot.score,
                    hotspot.commit_count,
                    hotspot.churn,
                    tuple(f"commit:{sha}" for sha in hotspot.evidence_shas),
                )
                for hotspot in hotspots
            )
        )
        for result in results:
            db.add(
                RiskScore(
                    id=new_id("rsk"),
                    workspace_id=actor.workspace_id,
                    repository_id=repository_id,
                    snapshot_id=snapshot.id,
                    path=result.path,
                    score=result.score,
                    features_json=result.features,
                    rationale=result.rationale,
                    evidence_ids=list(result.evidence_ids),
                )
            )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        stored = list(
            db.scalars(
                select(RiskScore)
                .where(RiskScore.snapshot_id == snapshot.id)
                .order_by(RiskScore.score.desc())
            )
        )
    return RiskResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha,
        scores=[
            RiskScoreResponse(
                path=item.path,
                score=item.score,
                features=item.features_json,
                rationale=item.rationale,
                evidence_ids=item.evidence_ids,
                model_version=item.model_version,
            )
            for item in stored
        ],
        limitations=[
            "Scores rank relative change risk; they are not defect probabilities.",
            "The transparent baseline uses hotspot, change-frequency, and churn signals only.",
        ],
    )


@router.get("/repositories/{repository_id}/impact", response_model=ImpactResponse)
def get_impact(
    repository_id: str,
    db: Database,
    actor: Actor,
    path: str = Query(min_length=1, max_length=1000),
) -> ImpactResponse:
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    relations = [
        ImpactRelation(
            item.left_path,
            item.right_path,
            "co_change",
            item.confidence,
            tuple(f"commit:{sha}" for sha in item.evidence_shas),
        )
        for item in db.scalars(
            select(CoChangeEdge).where(
                CoChangeEdge.snapshot_id == snapshot.id,
                (CoChangeEdge.left_path == path) | (CoChangeEdge.right_path == path),
            )
        )
    ]
    nodes = {
        item.id: item
        for item in db.scalars(select(GraphNode).where(GraphNode.snapshot_id == snapshot.id))
    }
    for edge in db.scalars(
        select(GraphEdge).where(GraphEdge.snapshot_id == snapshot.id, GraphEdge.type == "IMPORTS")
    ):
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source is None or target is None:
            continue
        if source.natural_key == path or target.natural_key == path:
            relations.append(
                ImpactRelation(
                    source.natural_key,
                    target.natural_key,
                    "imports",
                    edge.confidence,
                    (f"evidence:{edge.provenance_id}",),
                )
            )
    results = rank_impact(path, tuple(relations))
    return ImpactResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha,
        selected_path=path,
        impacted=[
            ImpactItemResponse(
                path=item.path,
                score=item.score,
                reasons=list(item.reasons),
                evidence_ids=list(item.evidence_ids),
            )
            for item in results
        ],
        limitations=[
            "Impact is a one-hop traversal of observed imports and repeated co-change.",
            "Absence from this result does not establish absence of runtime impact.",
        ],
    )


def _retrieval_documents(
    db: Database, repository_id: str, snapshot: RepositorySnapshot
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
        .where(RepositoryCommit.repository_id == repository_id)
        .order_by(RepositoryCommit.authored_at.desc())
        .limit(200)
    ):
        documents.append(RetrievalDocument(f"commit:{commit.sha}", "commit", commit.message))
    return tuple(documents)


@router.post("/chat/answers", response_model=GroundedAnswerResponse)
def create_grounded_answer(
    payload: GroundedAnswerCreate, request: Request, db: Database, actor: Actor
) -> GroundedAnswerResponse:
    _repository(db, payload.repository_id, actor)
    snapshot = _snapshot(db, payload.repository_id, actor)
    result = answer_question(
        payload.question, _retrieval_documents(db, payload.repository_id, snapshot)
    )
    answer = GroundedAnswer(
        id=new_id("ans"),
        workspace_id=actor.workspace_id,
        repository_id=payload.repository_id,
        question=payload.question,
        answer=result.answer,
        evidence_ids=list(result.evidence_ids),
        scope_json={"snapshot_id": snapshot.id, "snapshot_sha": snapshot.commit_sha},
        limitations=list(result.limitations),
        created_by=actor.user_id,
    )
    db.add(answer)
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="grounded_answer.created",
        resource_type="grounded_answer",
        resource_id=answer.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(answer)
    return GroundedAnswerResponse(
        id=answer.id,
        repository_id=answer.repository_id,
        question=answer.question,
        answer=answer.answer,
        evidence_ids=answer.evidence_ids,
        scope=answer.scope_json,
        limitations=answer.limitations,
        retrieval_version=answer.retrieval_version,
        created_at=answer.created_at,
    )


@router.post("/chat/answers/{answer_id}/feedback", response_model=AnswerFeedbackResponse)
def create_answer_feedback(
    answer_id: str,
    payload: AnswerFeedbackCreate,
    db: Database,
    actor: Actor,
) -> AnswerFeedbackResponse:
    answer = db.scalar(
        select(GroundedAnswer).where(
            GroundedAnswer.id == answer_id,
            GroundedAnswer.workspace_id == actor.workspace_id,
        )
    )
    if answer is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Answer was not found.")
    feedback = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.answer_id == answer_id, AnswerFeedback.user_id == actor.user_id
        )
    )
    if feedback is None:
        feedback = AnswerFeedback(
            id=new_id("fbk"),
            workspace_id=actor.workspace_id,
            answer_id=answer_id,
            user_id=actor.user_id,
            rating=payload.rating,
            comment=payload.comment,
        )
        db.add(feedback)
    else:
        feedback.rating = payload.rating
        feedback.comment = payload.comment
    db.commit()
    return AnswerFeedbackResponse(answer_id=answer_id, rating=payload.rating, recorded=True)
