from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..auth import require_user_id
from ..database import get_db
from ..ids import new_id
from ..models import Membership, Workspace
from ..schemas import WorkspaceCreate, WorkspaceResponse

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> Workspace:
    workspace = Workspace(id=new_id("ws"), name=payload.name.strip())
    db.add(workspace)
    db.add(Membership(workspace_id=workspace.id, user_id=user_id, role="owner"))
    db.commit()
    db.refresh(workspace)
    return workspace
