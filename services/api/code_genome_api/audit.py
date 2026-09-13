from sqlalchemy.orm import Session

from .ids import new_id
from .models import AuditEvent


def record_audit_event(
    db: Session,
    *,
    workspace_id: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    before_hash: str | None = None,
    after_hash: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=new_id("aud"),
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_hash=before_hash,
        after_hash=after_hash,
        request_id=request_id,
    )
    db.add(event)
    return event
