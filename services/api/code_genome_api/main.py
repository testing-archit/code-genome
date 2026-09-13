from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import SessionLocal
from .errors import AppError, app_error_handler, validation_error_handler
from .models import Membership, Workspace
from .routes import (
    analyses,
    architecture,
    delivery_reports,
    graph,
    health,
    intelligence,
    repositories,
    workspaces,
)


def _bootstrap_demo_workspace() -> None:
    if not get_settings().bootstrap_demo:
        return
    with SessionLocal() as db:
        if db.get(Workspace, "ws_demo") is None:
            db.add(Workspace(id="ws_demo", name="Genome Lab"))
            db.add(Membership(workspace_id="ws_demo", user_id="usr_demo", role="owner"))
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _bootstrap_demo_workspace()
    yield


app = FastAPI(
    title="CODE GENOME API",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-User-ID", "X-Workspace-ID"],
)
app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]


@app.middleware("http")
async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.request_id = request.headers.get("X-Request-ID", str(uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.include_router(health.router, prefix="/api/v1")
app.include_router(workspaces.router, prefix="/api/v1")
app.include_router(repositories.router, prefix="/api/v1")
app.include_router(analyses.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(architecture.router, prefix="/api/v1")
app.include_router(delivery_reports.router, prefix="/api/v1")
app.include_router(intelligence.router, prefix="/api/v1")
