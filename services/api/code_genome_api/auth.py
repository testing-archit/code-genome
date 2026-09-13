from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .errors import AppError
from .models import Membership, Workspace


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: str
    user_id: str
    role: str


def require_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    if not x_user_id:
        raise AppError(401, "UNAUTHENTICATED", "Authentication required", "X-User-ID is required.")
    return x_user_id


def require_workspace(
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[str, Depends(require_user_id)],
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> WorkspaceContext:
    if not x_workspace_id:
        raise AppError(
            401, "UNAUTHENTICATED", "Workspace context required", "X-Workspace-ID is required."
        )
    membership = db.scalar(
        select(Membership)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.workspace_id == x_workspace_id, Membership.user_id == user_id)
    )
    if membership is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Workspace was not found.")
    return WorkspaceContext(x_workspace_id, user_id, membership.role)


Database = Annotated[Session, Depends(get_db)]
Actor = Annotated[WorkspaceContext, Depends(require_workspace)]
