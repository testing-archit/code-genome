from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Header
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .errors import AppError
from .models import Membership, Workspace


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: str
    user_id: str
    role: str


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=300)


def _decode_oidc_token(token: str) -> str:
    settings = get_settings()
    assert settings.oidc_jwks_url
    assert settings.oidc_audience
    assert settings.oidc_issuer
    try:
        key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise AppError(
            401,
            "INVALID_TOKEN",
            "Authentication failed",
            "Bearer token validation failed.",
        ) from error
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 120:
        raise AppError(401, "INVALID_TOKEN", "Authentication failed", "Token subject is invalid.")
    return subject


def require_user_id(
    x_user_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    settings = get_settings()
    if settings.auth_mode == "development":
        if settings.environment == "production":
            raise AppError(
                503,
                "AUTH_CONFIGURATION_ERROR",
                "Authentication unavailable",
                "Development identity headers are disabled in production.",
            )
        if not x_user_id:
            raise AppError(
                401, "UNAUTHENTICATED", "Authentication required", "X-User-ID is required."
            )
        return x_user_id
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise AppError(
            401,
            "UNAUTHENTICATED",
            "Authentication required",
            "A bearer token is required.",
        )
    return _decode_oidc_token(token)


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
