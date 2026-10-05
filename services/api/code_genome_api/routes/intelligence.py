from typing import Any

from code_genome_intelligence import RiskInput, score_risks
from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth import Actor, Database
from ..errors import AppError
from ..ids import new_id
from ..models import (
    AnswerFeedback,
    FileHotspot,
    GroundedAnswer,
    Repository,
    RepositorySnapshot,
    RiskScore,
)
from ..schemas import (
    AnswerFeedbackCreate,
    AnswerFeedbackResponse,
    GroundedAnswerCreate,
    GroundedAnswerResponse,
    ImpactResponse,
    RiskResponse,
    RiskScoreResponse,
)
from ..services import ml
from ..services.grounding import produce_grounded_answer
from ..services.impact import impact_for_paths

router = APIRouter(tags=["intelligence"])


def grounded_answer_response(answer: GroundedAnswer) -> GroundedAnswerResponse:
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


def _learned_risk(repository_id: str, snapshot_sha: str, learned: dict[str, Any]) -> RiskResponse:
    labels: dict[str, str] = learned.get("feature_labels", {})
    champion = learned.get("champion") or "logistic_regression"
    metrics = learned.get("metrics", {}).get(champion, {})
    scores = []
    for item in learned.get("predictions", []):
        contributions = item.get("contributions", [])
        factors = ", ".join(
            f"{labels.get(name, name)} ({'+' if value >= 0 else ''}{value:.2f})"
            for name, value in contributions[:3]
        )
        scores.append(
            RiskScoreResponse(
                path=item["path"],
                score=item["probability"],
                features={labels.get(name, name): value for name, value in contributions},
                rationale=(
                    f"{item['band'].capitalize()} predicted defect-proneness "
                    f"(p={item['probability']:.2f}). Strongest factors: {factors}."
                ),
                evidence_ids=[f"commit:{sha}" for sha in item.get("evidence_shas", [])],
                model_version=f"{learned['model_version']}:{champion}",
            )
        )
    evaluation = (
        f"Held-out evaluation on the latest period: ROC-AUC {metrics['roc_auc']:.2f}, "
        f"average precision {metrics['average_precision']:.2f}."
        if "roc_auc" in metrics
        else "No held-out evaluation was possible for the latest period."
    )
    return RiskResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot_sha,
        scores=scores,
        limitations=[
            "Probabilities estimate whether a bug-fix commit will touch the file, learned from "
            "this repository's history; they are not proof of a defect.",
            evaluation,
            "Factor values are logistic-regression contributions to the log-odds.",
        ],
    )


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
    learned = ml.trained_result(db, snapshot, "defect_risk")
    if learned is not None:
        return _learned_risk(repository_id, snapshot.commit_sha, learned)
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
    results, limitations = impact_for_paths(db, snapshot, [path])
    return ImpactResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha,
        selected_path=path,
        impacted=results[path][:25],
        limitations=limitations,
    )


@router.post("/chat/answers", response_model=GroundedAnswerResponse)
def create_grounded_answer(
    payload: GroundedAnswerCreate, request: Request, db: Database, actor: Actor
) -> GroundedAnswerResponse:
    _repository(db, payload.repository_id, actor)
    snapshot = _snapshot(db, payload.repository_id, actor)
    answer = produce_grounded_answer(
        db,
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        repository_id=payload.repository_id,
        snapshot=snapshot,
        question=payload.question,
        request_id=request.state.request_id,
        channel=payload.channel,
        language=payload.language,
    )
    db.commit()
    db.refresh(answer)
    return grounded_answer_response(answer)


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
