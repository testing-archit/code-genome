"""Unified Software Genome Graph, SZZ bug history, and evolution timeline endpoints."""

from collections import Counter
from typing import Annotated, Literal

from code_genome_git import SZZ_VERSION
from fastapi import APIRouter, Query

from ..auth import Actor, Database
from ..errors import AppError
from ..schemas import (
    BugHistoryResponse,
    GenomeEdgeResponse,
    GenomeNodeResponse,
    GenomeResponse,
    GenomeScope,
    TimelineResponse,
)
from ..services import genome
from ..services.diffs import normalize_repository_path
from .insights import _facts

router = APIRouter(tags=["genome"])

GENOME_SOURCES = [
    "structural graph (imports, declarations, candidate calls)",
    "file manifest",
    "commit history and file changes",
    "co-change history",
    "SZZ-lite bug links",
    "static data-store and integration imports",
]


def _clean_path(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return normalize_repository_path(value)
    except ValueError as error:
        raise AppError(
            422, "INVALID_PATH", "Invalid path", "Path is not a valid repository path."
        ) from error


@router.get("/repositories/{repository_id}/genome", response_model=GenomeResponse)
def get_genome(
    repository_id: str,
    db: Database,
    actor: Actor,
    focus: Annotated[str | None, Query(max_length=1_000)] = None,
    limit: Annotated[int, Query(ge=10, le=1_000)] = 400,
) -> GenomeResponse:
    facts = _facts(db, repository_id, actor)
    focus_node_id: str | None = None
    expand: set[str] = set()
    if focus is not None and focus.strip():
        resolved = genome.resolve_focus(facts, focus)
        if resolved is None:
            raise AppError(
                404,
                "NOT_FOUND",
                "Focus not found",
                "No component or source file in the latest snapshot matches the focus.",
            )
        focus_node_id, expand = resolved
    graph = genome.build_full_genome(db, facts, include_symbols_for=expand)
    view = genome.select_view(graph, limit=limit, focus_node_id=focus_node_id)
    limitations = [
        "Inferred relationships (inferred: true) are candidates: CALLS are resolved "
        "statically without type checking; SEMANTICALLY_RELATED_TO is LSA similarity over "
        "path and symbol names; INTRODUCED_BUG comes from SZZ-lite; data-store and API edges "
        "come from imported client libraries and literal URLs.",
        "READS_FROM/WRITES_TO are chosen from ORM/driver method names in files that use the "
        "client; USES_DATASTORE means the direction could not be determined.",
        "OWNED_BY is the author with the most analysed commits touching the file or component; "
        "it is not an assignment of responsibility.",
        "Commits include the most recent ones plus every bug-fix and SZZ-linked commit, up to "
        f"{genome.MAX_COMMITS}. History covers only the analysed (possibly shallow) commits.",
    ]
    if view.truncated:
        limitations.append(
            f"Showing {len(view.nodes)} of {view.total_nodes} nodes; pass focus=<path or "
            "component> for a 2-hop neighbourhood or raise limit."
        )
    if not view.nodes:
        limitations.insert(0, genome.NO_EVIDENCE)
    return GenomeResponse(
        scope=GenomeScope(
            repository_id=repository_id,
            snapshot_id=facts.snapshot.id,
            snapshot_sha=facts.snapshot.commit_sha,
            analysis_version=facts.snapshot.analysis_version,
            sources=GENOME_SOURCES,
        ),
        version=genome.GENOME_VERSION,
        focus=focus.strip() if focus else None,
        focus_node_id=view.focus_node_id,
        nodes=[GenomeNodeResponse(**vars(node)) for node in view.nodes],
        edges=[GenomeEdgeResponse(**vars(edge)) for edge in view.edges],
        node_counts=dict(Counter(node.kind for node in view.nodes)),
        edge_counts=dict(Counter(edge.kind for edge in view.edges)),
        total_nodes=view.total_nodes,
        total_edges=view.total_edges,
        truncated=view.truncated,
        limitations=limitations,
    )


@router.get("/repositories/{repository_id}/bugs", response_model=BugHistoryResponse)
def get_bugs(
    repository_id: str,
    db: Database,
    actor: Actor,
    path: Annotated[str | None, Query(max_length=1_000)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> BugHistoryResponse:
    clean = _clean_path(path)
    facts = _facts(db, repository_id, actor)
    history = genome.bug_history(facts, path=clean, limit=limit)
    return BugHistoryResponse(
        repository_id=repository_id,
        snapshot_sha=facts.snapshot.commit_sha,
        analysis_version=SZZ_VERSION,
        fix_rule=genome.FIX_RULE,
        path=clean,
        **history,
    )


@router.get("/repositories/{repository_id}/timeline", response_model=TimelineResponse)
def get_timeline(
    repository_id: str,
    db: Database,
    actor: Actor,
    bucket: Literal["week", "month"] = "week",
    path: Annotated[str | None, Query(max_length=1_000)] = None,
) -> TimelineResponse:
    clean = _clean_path(path)
    facts = _facts(db, repository_id, actor)
    result = genome.timeline(facts, bucket, path=clean)
    return TimelineResponse(
        repository_id=repository_id,
        snapshot_sha=facts.snapshot.commit_sha,
        version=genome.TIMELINE_VERSION,
        bucket=bucket,
        path=clean,
        **result,
    )
