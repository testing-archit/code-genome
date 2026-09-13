import hashlib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from code_genome_analyzers import FileAnalysis, analyze_source
from code_genome_genome import GenomeGraph, build_structural_graph
from code_genome_git import (
    GitOperationError,
    GitRepository,
    RepositoryLimitError,
    clone_github_repository,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import (
    AnalysisRun,
    GraphEdge,
    GraphNode,
    ParseDiagnostic,
    Provenance,
    Repository,
    RepositorySnapshot,
    utc_now,
)

SessionFactory = Callable[[], Session]
logger = logging.getLogger(__name__)


class CloneRepository(Protocol):
    def __call__(
        self,
        clone_url: str,
        destination: Path,
        branch: str,
        *,
        depth: int,
        timeout_seconds: int,
    ) -> GitRepository: ...


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _set_progress(session_factory: SessionFactory, run_id: str, progress: float) -> None:
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None or run.state in {"SUCCEEDED", "FAILED"}:
            return
        run.state = "RUNNING"
        run.progress = progress
        run.started_at = run.started_at or utc_now()
        db.commit()


def _fail_run(
    session_factory: SessionFactory, run_id: str, code: str, detail: str, diagnostics: list[str]
) -> None:
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            return
        run.state = "FAILED"
        run.completed_at = utc_now()
        run.error_code = code
        run.error_detail = detail
        run.diagnostics = diagnostics
        db.commit()


def _published_snapshot(
    db: Session, repository_id: str, commit_sha: str
) -> RepositorySnapshot | None:
    return db.scalar(
        select(RepositorySnapshot).where(
            RepositorySnapshot.repository_id == repository_id,
            RepositorySnapshot.commit_sha == commit_sha,
            RepositorySnapshot.published_at.is_not(None),
        )
    )


def _persist_graph(
    db: Session,
    run: AnalysisRun,
    repository: Repository,
    tree_sha: str,
    graph: GenomeGraph,
) -> RepositorySnapshot:
    snapshot = RepositorySnapshot(
        id=_stable_id("snap", run.workspace_id, repository.id, graph.repository_sha),
        workspace_id=run.workspace_id,
        repository_id=repository.id,
        commit_sha=graph.repository_sha,
        tree_sha=tree_sha,
        run_id=run.id,
        analysis_version=graph.analysis_version,
        published_at=None,
    )
    db.add(snapshot)
    db.flush()

    for evidence_ref in graph.evidence:
        span = evidence_ref.span
        db.add(
            Provenance(
                id=evidence_ref.id,
                workspace_id=run.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                kind=evidence_ref.kind,
                repository_sha=evidence_ref.repository_sha,
                file_path=evidence_ref.path,
                start_line=span.start_line if span else None,
                start_column=span.start_column if span else None,
                end_line=span.end_line if span else None,
                end_column=span.end_column if span else None,
                extractor_version=evidence_ref.extractor_version,
            )
        )
    for graph_node in graph.nodes:
        db.add(
            GraphNode(
                id=graph_node.id,
                workspace_id=run.workspace_id,
                snapshot_id=snapshot.id,
                kind=graph_node.kind,
                natural_key=graph_node.natural_key,
                properties_json=graph_node.properties,
                evidence_ids=list(graph_node.evidence_ids),
            )
        )
    db.flush()
    for graph_edge in graph.edges:
        db.add(
            GraphEdge(
                id=graph_edge.id,
                workspace_id=run.workspace_id,
                snapshot_id=snapshot.id,
                type=graph_edge.kind,
                from_node=graph_edge.from_node,
                to_node=graph_edge.to_node,
                confidence=graph_edge.confidence,
                provenance_id=graph_edge.evidence_id,
            )
        )
    for ordinal, graph_diagnostic in enumerate(graph.diagnostics):
        span = graph_diagnostic.span
        db.add(
            ParseDiagnostic(
                id=_stable_id(
                    "diag",
                    repository.id,
                    graph.repository_sha,
                    graph_diagnostic.path,
                    graph_diagnostic.code,
                    span,
                    ordinal,
                ),
                workspace_id=run.workspace_id,
                snapshot_id=snapshot.id,
                code=graph_diagnostic.code,
                message=graph_diagnostic.message,
                file_path=graph_diagnostic.path,
                start_line=span.start_line if span else None,
                start_column=span.start_column if span else None,
                end_line=span.end_line if span else None,
                end_column=span.end_column if span else None,
                provenance_id=graph_diagnostic.evidence_id,
            )
        )
    snapshot.published_at = utc_now()
    return snapshot


def run_structural_analysis(
    run_id: str,
    session_factory: SessionFactory = SessionLocal,
    clone_repository: CloneRepository = clone_github_repository,
) -> None:
    """Publish a pinned structural snapshot, or record a safe terminal failure."""
    settings = get_settings()
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None or run.state in {"SUCCEEDED", "FAILED"}:
            return
        repository = db.scalar(
            select(Repository).where(
                Repository.id == run.repository_id,
                Repository.workspace_id == run.workspace_id,
            )
        )
        if repository is None:
            _fail_run(
                session_factory,
                run_id,
                "REPOSITORY_NOT_FOUND",
                "The repository is no longer available in this workspace.",
                [],
            )
            return
        clone_url = repository.clone_url
        branch = run.requested_refs[0] if run.requested_refs else repository.default_branch

    _set_progress(session_factory, run_id, 0.1)
    try:
        with tempfile.TemporaryDirectory(prefix="code-genome-analysis-") as temporary_directory:
            git_repository = clone_repository(
                clone_url,
                Path(temporary_directory) / "repository.git",
                branch,
                depth=settings.clone_depth,
                timeout_seconds=settings.clone_timeout_seconds,
            )
            source_snapshot = git_repository.read_source_snapshot(
                branch,
                max_files=settings.max_source_files,
                max_file_bytes=settings.max_source_file_bytes,
                max_total_bytes=settings.max_source_total_bytes,
            )
            _set_progress(session_factory, run_id, 0.4)

            with session_factory() as db:
                existing = _published_snapshot(db, run.repository_id, source_snapshot.commit_sha)
                if existing:
                    current_run = db.get(AnalysisRun, run_id)
                    if current_run:
                        current_run.snapshot_sha = existing.commit_sha
                        current_run.version = existing.analysis_version
                        current_run.state = "SUCCEEDED"
                        current_run.progress = 1.0
                        current_run.completed_at = utc_now()
                        current_run.diagnostics = [
                            "Reused the existing immutable snapshot for this repository commit."
                        ]
                        db.commit()
                    return

            analyses: list[FileAnalysis] = [
                analyze_source(source_file.path, source_file.content)
                for source_file in source_snapshot.files
            ]
            graph = build_structural_graph(run.repository_id, source_snapshot.commit_sha, analyses)
            _set_progress(session_factory, run_id, 0.7)

            with session_factory() as db:
                current_run = db.get(AnalysisRun, run_id)
                current_repository = db.get(Repository, run.repository_id)
                if current_run is None or current_repository is None:
                    raise GitOperationError("Analysis scope disappeared before publication.")
                _persist_graph(db, current_run, current_repository, source_snapshot.tree_sha, graph)
                current_run.snapshot_sha = source_snapshot.commit_sha
                current_run.version = graph.analysis_version
                current_run.state = "SUCCEEDED"
                current_run.progress = 1.0
                current_run.completed_at = utc_now()
                current_run.diagnostics = [
                    f"Published {len(graph.nodes)} nodes and {len(graph.edges)} edges from "
                    f"{len(analyses)} source files.",
                    f"Recorded {len(graph.diagnostics)} structural diagnostics.",
                    "Skipped "
                    f"{len(source_snapshot.skipped_oversized_files)} oversized source files.",
                ]
                db.commit()
    except RepositoryLimitError:
        logger.warning("repository_limit_exceeded", extra={"analysis_run_id": run_id})
        _fail_run(
            session_factory,
            run_id,
            "REPOSITORY_LIMIT_EXCEEDED",
            "Repository source exceeded the configured analysis limits.",
            ["No graph snapshot was published."],
        )
    except GitOperationError:
        logger.warning("git_operation_failed", extra={"analysis_run_id": run_id})
        _fail_run(
            session_factory,
            run_id,
            "GIT_OPERATION_FAILED",
            "The pinned repository source could not be retrieved.",
            ["No graph snapshot was published."],
        )
    except Exception:
        logger.exception("structural_analysis_failed", extra={"analysis_run_id": run_id})
        _fail_run(
            session_factory,
            run_id,
            "ANALYSIS_FAILED",
            "Structural analysis failed before publication.",
            ["No partial graph snapshot was published."],
        )


def run_analysis(run_id: str, session_factory: SessionFactory = SessionLocal) -> None:
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        simulate_failure = bool(run and run.simulate_failure)
    if simulate_failure:
        from .analysis import run_fake_analysis

        run_fake_analysis(run_id, session_factory)
        return
    run_structural_analysis(run_id, session_factory)
