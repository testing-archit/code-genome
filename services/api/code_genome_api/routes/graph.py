import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import and_, select

from ..auth import Actor, Database
from ..errors import AppError
from ..models import (
    GraphEdge,
    GraphNode,
    ParseDiagnostic,
    Provenance,
    Repository,
    RepositorySnapshot,
)
from ..schemas import (
    DiagnosticResponse,
    EvidenceResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphProjectionResponse,
    GraphScope,
)

router = APIRouter(tags=["graph"])


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = base64.urlsafe_b64decode(cursor.encode()).decode()
        offset = int(value)
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise AppError(
            400, "INVALID_CURSOR", "Invalid cursor", "Graph cursor is invalid."
        ) from error
    if offset < 0:
        raise AppError(400, "INVALID_CURSOR", "Invalid cursor", "Graph cursor is invalid.")
    return offset


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


@router.get("/repositories/{repository_id}/graph", response_model=GraphProjectionResponse)
def get_graph(
    repository_id: str,
    db: Database,
    actor: Actor,
    snapshot_sha: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 250,
) -> GraphProjectionResponse:
    repository = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == actor.workspace_id
        )
    )
    if repository is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Repository was not found.")

    snapshot_query = select(RepositorySnapshot).where(
        RepositorySnapshot.repository_id == repository_id,
        RepositorySnapshot.workspace_id == actor.workspace_id,
        RepositorySnapshot.published_at.is_not(None),
    )
    if snapshot_sha:
        snapshot_query = snapshot_query.where(RepositorySnapshot.commit_sha == snapshot_sha)
    snapshot = db.scalar(snapshot_query.order_by(RepositorySnapshot.published_at.desc()).limit(1))
    if snapshot is None:
        raise AppError(
            404,
            "NOT_FOUND",
            "Published graph not found",
            "No published structural graph exists in the selected repository scope.",
        )

    offset = _decode_cursor(cursor)
    page = list(
        db.scalars(
            select(GraphNode)
            .where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == actor.workspace_id,
            )
            .order_by(GraphNode.kind, GraphNode.natural_key)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    has_more = len(page) > limit
    page = page[:limit]
    node_ids = [item.id for item in page]
    edges = (
        list(
            db.scalars(
                select(GraphEdge)
                .where(
                    GraphEdge.snapshot_id == snapshot.id,
                    GraphEdge.workspace_id == actor.workspace_id,
                    and_(
                        GraphEdge.from_node.in_(node_ids),
                        GraphEdge.to_node.in_(node_ids),
                    ),
                )
                .order_by(GraphEdge.type, GraphEdge.id)
            )
        )
        if node_ids
        else []
    )
    diagnostics = list(
        db.scalars(
            select(ParseDiagnostic)
            .where(
                ParseDiagnostic.snapshot_id == snapshot.id,
                ParseDiagnostic.workspace_id == actor.workspace_id,
            )
            .order_by(ParseDiagnostic.file_path, ParseDiagnostic.start_line)
            .limit(250)
        )
    )
    return GraphProjectionResponse(
        scope=GraphScope(
            repository_id=repository_id,
            snapshot_id=snapshot.id,
            snapshot_sha=snapshot.commit_sha,
            analysis_version=snapshot.analysis_version,
            sources=["source_file", "source_range"],
        ),
        nodes=[
            GraphNodeResponse(
                id=item.id,
                kind=item.kind,
                natural_key=item.natural_key,
                properties=item.properties_json,
                evidence_ids=item.evidence_ids,
            )
            for item in page
        ],
        edges=[
            GraphEdgeResponse(
                id=item.id,
                type=item.type,
                from_node=item.from_node,
                to_node=item.to_node,
                confidence=item.confidence,
                evidence_id=item.provenance_id,
            )
            for item in edges
        ],
        diagnostics=[
            DiagnosticResponse(
                id=item.id,
                code=item.code,
                message=item.message,
                path=item.file_path,
                start_line=item.start_line,
                start_column=item.start_column,
                end_line=item.end_line,
                end_column=item.end_column,
                evidence_id=item.provenance_id,
            )
            for item in diagnostics
        ],
        next_cursor=_encode_cursor(offset + limit) if has_more else None,
        limitations=(
            ["Edges are limited to relationships between nodes in this page."]
            if has_more or offset
            else []
        ),
    )


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(evidence_id: str, db: Database, actor: Actor) -> EvidenceResponse:
    item = db.scalar(
        select(Provenance)
        .join(RepositorySnapshot, RepositorySnapshot.id == Provenance.snapshot_id)
        .where(
            Provenance.id == evidence_id,
            Provenance.workspace_id == actor.workspace_id,
            RepositorySnapshot.published_at.is_not(None),
        )
    )
    if item is None:
        raise AppError(404, "NOT_FOUND", "Resource not found", "Evidence was not found.")
    return EvidenceResponse(
        id=item.id,
        kind=item.kind,
        repository_sha=item.repository_sha,
        path=item.file_path,
        start_line=item.start_line,
        start_column=item.start_column,
        end_line=item.end_line,
        end_column=item.end_column,
        extractor_version=item.extractor_version,
        observed_at=item.observed_at,
    )
