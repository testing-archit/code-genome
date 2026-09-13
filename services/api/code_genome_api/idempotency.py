import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import AppError
from .ids import new_id
from .models import IdempotencyRecord


def fingerprint(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def find_idempotent_resource(
    db: Session, workspace_id: str, endpoint: str, key: str, request_fingerprint: str
) -> str | None:
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.workspace_id == workspace_id,
            IdempotencyRecord.endpoint == endpoint,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None
    if record.request_fingerprint != request_fingerprint:
        raise AppError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "Idempotency key conflict",
            "This Idempotency-Key was already used with a different request.",
        )
    return record.resource_id


def record_idempotency(
    db: Session,
    workspace_id: str,
    endpoint: str,
    key: str,
    resource_type: str,
    resource_id: str,
    request_fingerprint: str,
) -> None:
    db.add(
        IdempotencyRecord(
            id=new_id("idem"),
            workspace_id=workspace_id,
            endpoint=endpoint,
            key=key,
            resource_type=resource_type,
            resource_id=resource_id,
            request_fingerprint=request_fingerprint,
        )
    )
