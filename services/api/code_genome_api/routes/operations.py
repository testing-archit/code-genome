import csv
import io
import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import delete, select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..models import (
    AnswerFeedback,
    AuditEvent,
    DeliveryAssessment,
    DeliveryClaim,
    DeliveryReport,
    GroundedAnswer,
    UnreportedChange,
    utc_now,
)
from ..schemas import RetentionRunCreate, RetentionRunResponse

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["operations"])


def csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _require_admin(workspace_id: str, actor: Actor) -> None:
    if workspace_id != actor.workspace_id:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Workspace was not found.")
    if actor.role not in {"owner", "admin"}:
        raise AppError(
            403, "FORBIDDEN", "Admin role required", "This operation requires admin access."
        )


@router.post("/retention/run", response_model=RetentionRunResponse)
def run_retention(
    workspace_id: str,
    payload: RetentionRunCreate,
    request: Request,
    db: Database,
    actor: Actor,
) -> RetentionRunResponse:
    _require_admin(workspace_id, actor)
    cutoff = utc_now() - timedelta(days=get_settings().retention_days)
    report_ids = list(
        db.scalars(
            select(DeliveryReport.id).where(
                DeliveryReport.workspace_id == workspace_id,
                DeliveryReport.created_at < cutoff,
            )
        )
    )
    answer_ids = list(
        db.scalars(
            select(GroundedAnswer.id).where(
                GroundedAnswer.workspace_id == workspace_id,
                GroundedAnswer.created_at < cutoff,
            )
        )
    )
    if not payload.dry_run:
        claim_ids = list(
            db.scalars(select(DeliveryClaim.id).where(DeliveryClaim.report_id.in_(report_ids)))
        )
        db.execute(delete(DeliveryAssessment).where(DeliveryAssessment.claim_id.in_(claim_ids)))
        db.execute(delete(UnreportedChange).where(UnreportedChange.report_id.in_(report_ids)))
        db.execute(delete(DeliveryClaim).where(DeliveryClaim.report_id.in_(report_ids)))
        db.execute(delete(DeliveryReport).where(DeliveryReport.id.in_(report_ids)))
        db.execute(delete(AnswerFeedback).where(AnswerFeedback.answer_id.in_(answer_ids)))
        db.execute(delete(GroundedAnswer).where(GroundedAnswer.id.in_(answer_ids)))
        record_audit_event(
            db,
            workspace_id=workspace_id,
            actor_id=actor.user_id,
            action="retention.executed",
            resource_type="workspace",
            resource_id=workspace_id,
            request_id=request.state.request_id,
            after_hash=None,
        )
        db.commit()
    return RetentionRunResponse(
        cutoff=cutoff,
        dry_run=payload.dry_run,
        delivery_reports=len(report_ids),
        grounded_answers=len(answer_ids),
    )


@router.get("/audit-events")
def export_audit_events(
    workspace_id: str,
    request: Request,
    db: Database,
    actor: Actor,
    format: Literal["json", "csv"] = "json",
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> Response:
    _require_admin(workspace_id, actor)
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
    )
    record_audit_event(
        db,
        workspace_id=workspace_id,
        actor_id=actor.user_id,
        action="audit.exported",
        resource_type="workspace",
        resource_id=workspace_id,
        request_id=request.state.request_id,
    )
    db.commit()
    rows = [
        {
            "id": event.id,
            "actor_id": event.actor_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "before_hash": event.before_hash,
            "after_hash": event.after_hash,
            "request_id": event.request_id,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
    headers = {"Content-Disposition": f'attachment; filename="audit-{workspace_id}.{format}"'}
    if format == "json":
        return Response(
            json.dumps(rows, separators=(",", ":")), media_type="application/json", headers=headers
        )
    output = io.StringIO()
    fieldnames = list(rows[0]) if rows else ["id", "action", "created_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows({key: csv_safe(value) for key, value in row.items()} for row in rows)
    return Response(output.getvalue(), media_type="text/csv", headers=headers)
