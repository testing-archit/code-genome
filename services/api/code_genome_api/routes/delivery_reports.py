from typing import Annotated

from code_genome_delivery_auditor import EvidenceCandidate, assess_claim, parse_claims
from fastapi import APIRouter, Header, Request, Response, status
from sqlalchemy import select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..errors import AppError
from ..idempotency import find_idempotent_resource, fingerprint, record_idempotency
from ..ids import new_id
from ..models import (
    DeliveryAssessment,
    DeliveryClaim,
    DeliveryReport,
    FileChange,
    Repository,
    RepositoryCommit,
    UnreportedChange,
)
from ..schemas import (
    DeliveryAssessmentResponse,
    DeliveryClaimResponse,
    DeliveryReportCreate,
    DeliveryReportResponse,
    DeliveryScope,
    UnreportedChangeResponse,
)

router = APIRouter(prefix="/delivery-reports", tags=["delivery-auditor"])


def _report_for_actor(db: Database, report_id: str, actor: Actor) -> DeliveryReport:
    report = db.scalar(
        select(DeliveryReport).where(
            DeliveryReport.id == report_id, DeliveryReport.workspace_id == actor.workspace_id
        )
    )
    if report is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Delivery report was not found.")
    return report


def _serialize_report(db: Database, report: DeliveryReport) -> DeliveryReportResponse:
    claims = list(
        db.scalars(
            select(DeliveryClaim)
            .where(DeliveryClaim.report_id == report.id)
            .order_by(DeliveryClaim.ordinal)
        )
    )
    assessments = {
        item.claim_id: item
        for item in db.scalars(
            select(DeliveryAssessment).where(
                DeliveryAssessment.claim_id.in_([claim.id for claim in claims])
            )
        )
    }
    unreported = list(
        db.scalars(
            select(UnreportedChange)
            .where(UnreportedChange.report_id == report.id)
            .order_by(UnreportedChange.materiality.desc(), UnreportedChange.path)
        )
    )
    pending = any(claim.id not in assessments for claim in claims)
    scope = DeliveryScope.model_validate(report.scope_json)
    return DeliveryReportResponse(
        id=report.id,
        repository_id=report.repository_id,
        raw_text=report.raw_text,
        scope=scope,
        submitted_by=report.submitted_by,
        parser_version=report.parser_version,
        created_at=report.created_at,
        claims=[
            DeliveryClaimResponse(
                id=claim.id,
                ordinal=claim.ordinal,
                original_text=claim.original_text,
                start_offset=claim.start_offset,
                end_offset=claim.end_offset,
                claim_type=claim.claim_type,
                assessment=(
                    DeliveryAssessmentResponse.model_validate(
                        assessments[claim.id], from_attributes=True
                    )
                    if claim.id in assessments
                    else None
                ),
            )
            for claim in claims
        ],
        unreported_changes=[
            UnreportedChangeResponse.model_validate(item, from_attributes=True)
            for item in unreported
        ],
        limitations=(
            ["Assessment has not run yet."]
            if pending
            else [
                "Repository history cannot prove CI or deployment-provider outcomes.",
                "Branch membership is bounded by the analyzed snapshot; date filters are exact.",
            ]
        ),
    )


@router.post("", response_model=DeliveryReportResponse, status_code=status.HTTP_201_CREATED)
def create_delivery_report(
    payload: DeliveryReportCreate,
    request: Request,
    db: Database,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=160)],
) -> DeliveryReportResponse:
    if payload.scope.from_ > payload.scope.to:
        raise AppError(422, "INVALID_SCOPE", "Invalid report scope", "from must precede to.")
    repository = db.scalar(
        select(Repository).where(
            Repository.id == payload.repository_id,
            Repository.workspace_id == actor.workspace_id,
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")
    endpoint = "POST:/delivery-reports"
    body = payload.model_dump(mode="json", by_alias=True)
    request_fingerprint = fingerprint(body)
    existing_id = find_idempotent_resource(
        db, actor.workspace_id, endpoint, idempotency_key, request_fingerprint
    )
    if existing_id:
        return _serialize_report(db, _report_for_actor(db, existing_id, actor))
    try:
        drafts = parse_claims(payload.text)
    except ValueError as error:
        raise AppError(422, "INVALID_REPORT", "Invalid delivery report", str(error)) from error
    report = DeliveryReport(
        id=new_id("rpt"),
        workspace_id=actor.workspace_id,
        repository_id=repository.id,
        raw_text=payload.text,
        scope_json=body["scope"],
        submitted_by=actor.user_id,
    )
    db.add(report)
    for draft in drafts:
        db.add(
            DeliveryClaim(
                id=new_id("clm"),
                workspace_id=actor.workspace_id,
                report_id=report.id,
                ordinal=draft.ordinal,
                original_text=draft.text,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                claim_type=draft.claim_type,
            )
        )
    record_idempotency(
        db,
        actor.workspace_id,
        endpoint,
        idempotency_key,
        "delivery_report",
        report.id,
        request_fingerprint,
    )
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="delivery_report.created",
        resource_type="delivery_report",
        resource_id=report.id,
        request_id=request.state.request_id,
        after_hash=fingerprint({"repository_id": repository.id, "scope": body["scope"]}),
    )
    db.commit()
    return _serialize_report(db, report)


def _evidence_candidates(
    db: Database, report: DeliveryReport
) -> tuple[tuple[EvidenceCandidate, ...], list[FileChange]]:
    scope = DeliveryScope.model_validate(report.scope_json)
    changes = list(
        db.scalars(
            select(FileChange)
            .where(
                FileChange.repository_id == report.repository_id,
                FileChange.workspace_id == report.workspace_id,
                FileChange.authored_at >= scope.from_,
                FileChange.authored_at <= scope.to,
            )
            .order_by(FileChange.authored_at.desc())
            .limit(5000)
        )
    )
    shas = {change.commit_sha for change in changes}
    messages = {
        item.sha: item.message
        for item in db.scalars(
            select(RepositoryCommit).where(
                RepositoryCommit.repository_id == report.repository_id,
                RepositoryCommit.sha.in_(shas),
            )
        )
    }
    candidates = tuple(
        EvidenceCandidate(
            id=f"change:{change.commit_sha}:{change.path}",
            kind="repository_change",
            text=f"{change.path} {messages.get(change.commit_sha, '')}",
        )
        for change in changes
    )
    return candidates, changes


@router.post("/{report_id}/assessments", response_model=DeliveryReportResponse)
def assess_delivery_report(
    report_id: str, request: Request, db: Database, actor: Actor
) -> DeliveryReportResponse:
    report = _report_for_actor(db, report_id, actor)
    claims = list(
        db.scalars(
            select(DeliveryClaim)
            .where(DeliveryClaim.report_id == report.id)
            .order_by(DeliveryClaim.ordinal)
        )
    )
    candidates, changes = _evidence_candidates(db, report)
    cited_paths: set[str] = set()
    for claim in claims:
        existing = db.scalar(
            select(DeliveryAssessment).where(DeliveryAssessment.claim_id == claim.id)
        )
        if existing is not None:
            cited_paths.update(evidence.rsplit(":", 1)[-1] for evidence in existing.evidence_ids)
            continue
        draft = parse_claims(claim.original_text, max_claims=1)[0]
        draft = type(draft)(
            claim.ordinal,
            claim.original_text,
            claim.start_offset,
            claim.end_offset,
            claim.claim_type,
        )
        result = assess_claim(draft, candidates)
        cited_paths.update(evidence.rsplit(":", 1)[-1] for evidence in result.evidence_ids)
        db.add(
            DeliveryAssessment(
                id=new_id("asm"),
                workspace_id=actor.workspace_id,
                claim_id=claim.id,
                status=result.status,
                confidence=result.confidence,
                rationale=result.rationale,
                evidence_ids=list(result.evidence_ids),
                limitations=list(result.limitations),
            )
        )
    by_path: dict[str, list[FileChange]] = {}
    for change in changes:
        by_path.setdefault(change.path, []).append(change)
    maximum_churn = max(
        (sum(item.churn for item in values) for values in by_path.values()), default=1
    )
    for path, path_changes in sorted(by_path.items()):
        if path in cited_paths:
            continue
        evidence_ids = [f"change:{item.commit_sha}:{item.path}" for item in path_changes[:5]]
        churn = sum(item.churn for item in path_changes)
        db.add(
            UnreportedChange(
                id=new_id("chg"),
                workspace_id=actor.workspace_id,
                report_id=report.id,
                path=path,
                evidence_ids=evidence_ids,
                materiality=round(churn / maximum_churn, 4),
                explanation=(
                    f"Observed in {len(path_changes)} scoped commit(s) "
                    "but not cited by a claim."
                ),
            )
        )
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="delivery_report.assessed",
        resource_type="delivery_report",
        resource_id=report.id,
        request_id=request.state.request_id,
    )
    db.commit()
    return _serialize_report(db, report)


@router.get("/{report_id}", response_model=DeliveryReportResponse)
def get_delivery_report(report_id: str, db: Database, actor: Actor) -> DeliveryReportResponse:
    return _serialize_report(db, _report_for_actor(db, report_id, actor))


@router.get("/{report_id}/download")
def download_delivery_report(report_id: str, db: Database, actor: Actor) -> Response:
    result = _serialize_report(db, _report_for_actor(db, report_id, actor))
    lines = [
        f"# Delivery audit {result.id}",
        "",
        f"Repository: `{result.repository_id}`",
        f"Scope: {result.scope.from_.isoformat()} to {result.scope.to.isoformat()}",
        "",
        "## Claims",
        "",
    ]
    for claim in result.claims:
        assessment = claim.assessment
        lines.extend(
            [
                f"### {claim.ordinal + 1}. {claim.original_text}",
                "",
                f"Status: {assessment.status if assessment else 'PENDING'}",
                f"Confidence: {assessment.confidence if assessment else 0:.2f}",
                "Evidence: "
                + (
                    ", ".join(assessment.evidence_ids)
                    if assessment and assessment.evidence_ids
                    else "none"
                ),
                "Limitations: "
                + (
                    "; ".join(assessment.limitations)
                    if assessment and assessment.limitations
                    else "none"
                ),
                "",
            ]
        )
    lines.extend(["## Unreported changes", ""])
    lines.extend(
        f"- `{item.path}` ({item.materiality:.2f}): {item.explanation}"
        for item in result.unreported_changes
    )
    return Response(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="delivery-audit-{result.id}.md"'},
    )
