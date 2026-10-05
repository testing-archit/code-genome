import fcntl
import hashlib
import logging
import os
import shutil
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from code_genome_analyzers import ANALYZER_VERSION, FileAnalysis, analyze_source
from code_genome_evolution import EvolutionResult, analyze_evolution, mine_commit_changes
from code_genome_genome import GRAPH_BUILDER_VERSION, GenomeGraph, build_structural_graph
from code_genome_git import (
    GitCredential,
    GitOperationError,
    GitRepository,
    RepositoryBranchRef,
    RepositoryLimitError,
    RepositoryManifestFile,
    RepositorySourceFile,
    RepositorySourceSnapshot,
    SzzResult,
    sync_github_repository,
    trace_bug_introductions,
)
from code_genome_git import (
    RepositoryCommit as GitCommit,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import record_audit_event
from ..config import get_settings
from ..database import SessionLocal
from ..models import (
    AnalysisRun,
    BranchRef,
    BugLink,
    CoChangeEdge,
    FileChange,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    KnowledgeChunkRecord,
    ModuleCandidate,
    ParseDiagnostic,
    Provenance,
    Repository,
    RepositoryCommit,
    RepositoryConnection,
    RepositorySnapshot,
    utc_now,
)
from . import knowledge
from .credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialDecryptionError,
    EncryptedCredential,
)
from .knowledge import ChunkKind
from .ml import train_snapshot_models

SessionFactory = Callable[[], Session]
# A snapshot built by another analyzer version is rebuilt in place on re-analysis.
CURRENT_GRAPH_VERSION = f"{GRAPH_BUILDER_VERSION}+{ANALYZER_VERSION}"
GRAPH_EVIDENCE_KINDS = ("source_file", "source_range")
logger = logging.getLogger(__name__)
SZZ_MAX_FIX_COMMITS = 60


class SyncRepository(Protocol):
    def __call__(
        self,
        clone_url: str,
        destination: Path,
        branch: str,
        *,
        depth: int,
        timeout_seconds: int,
        credential: GitCredential | None,
    ) -> GitRepository: ...


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _set_progress(
    session_factory: SessionFactory,
    run_id: str,
    progress: float,
    stage: str | None = None,
    counts: dict[str, int] | None = None,
    message: str | None = None,
) -> None:
    """Record the live stage, counts, and a human-readable line the UI can poll."""
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None or run.state in {"SUCCEEDED", "FAILED"}:
            return
        run.state = "RUNNING"
        run.progress = progress
        run.started_at = run.started_at or utc_now()
        if stage is not None:
            run.stage = stage
        if counts:
            run.progress_counts = {**(run.progress_counts or {}), **counts}
        if message:
            run.diagnostics = [*(run.diagnostics or []), message][-20:]
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
    db: Session, workspace_id: str, repository_id: str, commit_sha: str
) -> RepositorySnapshot | None:
    return db.scalar(
        select(RepositorySnapshot).where(
            RepositorySnapshot.workspace_id == workspace_id,
            RepositorySnapshot.repository_id == repository_id,
            RepositorySnapshot.commit_sha == commit_sha,
            RepositorySnapshot.published_at.is_not(None),
        )
    )


def _mirror_path(root: str, workspace_id: str, repository_id: str) -> Path:
    workspace_key = hashlib.sha256(workspace_id.encode()).hexdigest()[:24]
    repository_key = hashlib.sha256(repository_id.encode()).hexdigest()[:24]
    return Path(root).resolve() / workspace_key / f"{repository_key}.git"


def delete_repository_mirror(workspace_id: str, repository_id: str) -> bool:
    root = Path(get_settings().mirror_root).resolve()
    target = _mirror_path(str(root), workspace_id, repository_id)
    if not target.is_relative_to(root) or target.suffix != ".git":
        raise ValueError("Refusing to delete a mirror outside the configured root")
    if not target.exists():
        return False
    lock_path = target.with_name(f"{target.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if target.exists():
            shutil.rmtree(target)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    lock_path.unlink(missing_ok=True)
    with suppress(OSError):
        target.parent.rmdir()
    return True


def _load_git_credential(
    connection: RepositoryConnection | None,
    *,
    workspace_id: str,
    repository_id: str,
) -> GitCredential | None:
    if connection is None:
        return None
    settings = get_settings()
    key = settings.credential_encryption_key
    if (
        key is None
        or not key.get_secret_value()
        or connection.key_version != settings.credential_key_version
    ):
        raise CredentialConfigurationError("Repository credential key is unavailable.")
    token = CredentialCipher(key.get_secret_value()).decrypt(
        EncryptedCredential(
            ciphertext=connection.credential_ciphertext,
            nonce=connection.credential_nonce,
        ),
        workspace_id,
        repository_id,
    )
    return GitCredential(token=token)


def _as_utc(value: datetime | str) -> datetime:
    """Return the UTC instant for a commit timestamp.

    Git records the author's offset (e.g. ``+05:30``). Some databases (SQLite) drop the
    offset on write and keep the wall time, which would silently shift the instant, so
    timestamps are normalized to UTC before they are stored. Naive values are already UTC
    (that is how they come back from SQLite).
    """
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _persist_history(
    db: Session,
    repository: Repository,
    refs: tuple[RepositoryBranchRef, ...],
    commits: tuple[GitCommit, ...],
) -> None:
    observed_at = utc_now()
    existing_refs = {
        item.name: item
        for item in db.scalars(
            select(BranchRef).where(
                BranchRef.workspace_id == repository.workspace_id,
                BranchRef.repository_id == repository.id,
            )
        )
    }
    for ref_item in refs:
        current = existing_refs.get(ref_item.name)
        if current:
            current.head_sha = ref_item.head_sha
            current.observed_at = observed_at
        else:
            db.add(
                BranchRef(
                    id=_stable_id("ref", repository.id, ref_item.name),
                    workspace_id=repository.workspace_id,
                    repository_id=repository.id,
                    name=ref_item.name,
                    head_sha=ref_item.head_sha,
                    observed_at=observed_at,
                )
            )
    existing_commits = {
        item.sha: item
        for item in db.scalars(
            select(RepositoryCommit).where(
                RepositoryCommit.workspace_id == repository.workspace_id,
                RepositoryCommit.repository_id == repository.id,
                RepositoryCommit.sha.in_([commit_item.sha for commit_item in commits]),
            )
        )
    }
    corrected = 0
    for commit_item in commits:
        authored_at = _as_utc(commit_item.authored_at)
        current_commit = existing_commits.get(commit_item.sha)
        if current_commit is not None:
            # Rows written before UTC normalization hold the author's wall time; the Git
            # object is the immutable source, so re-ingesting it corrects the instant.
            if _as_utc(current_commit.authored_at) != authored_at:
                current_commit.authored_at = authored_at
                corrected += 1
            continue
        db.add(
            RepositoryCommit(
                id=_stable_id("cmt", repository.id, commit_item.sha),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                sha=commit_item.sha,
                parent_shas=list(commit_item.parent_shas),
                author_name=commit_item.author_name,
                author_email=commit_item.author_email,
                authored_at=authored_at,
                message=commit_item.message,
                observed_at=observed_at,
            )
        )
    if corrected:
        logger.info(
            "commit_timestamps_normalized",
            extra={"repository_id": repository.id, "corrected": corrected},
        )


def _persist_manifest(
    db: Session,
    repository: Repository,
    snapshot: RepositorySnapshot,
    source_snapshot: RepositorySourceSnapshot,
) -> None:
    existing = db.scalar(
        select(FileManifestEntry.id).where(
            FileManifestEntry.workspace_id == repository.workspace_id,
            FileManifestEntry.snapshot_id == snapshot.id,
        )
    )
    if existing:
        return
    analyzed_paths = {item.path for item in source_snapshot.files}
    for item in source_snapshot.manifest:
        db.add(
            FileManifestEntry(
                id=_stable_id("file", snapshot.id, item.path),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                path=item.path,
                blob_sha=item.blob_sha,
                mode=item.mode,
                size=item.size,
                analyzed=item.path in analyzed_paths,
            )
        )


def _persist_evolution(
    db: Session,
    repository: Repository,
    snapshot: RepositorySnapshot,
    evolution: EvolutionResult,
) -> None:
    if db.scalar(select(ModuleCandidate.id).where(ModuleCandidate.snapshot_id == snapshot.id)):
        _normalize_change_times(db, repository, snapshot, evolution)
        return
    for commit in evolution.commits:
        per_file_churn = max(1, commit.churn // max(1, len(commit.files)))
        for path in commit.files:
            db.add(
                FileChange(
                    id=_stable_id("chg", snapshot.id, commit.sha, path),
                    workspace_id=repository.workspace_id,
                    repository_id=repository.id,
                    snapshot_id=snapshot.id,
                    commit_sha=commit.sha,
                    path=path,
                    authored_at=_as_utc(commit.authored_at),
                    churn=per_file_churn,
                )
            )
    for co_change in evolution.co_changes:
        db.add(
            CoChangeEdge(
                id=_stable_id("coe", snapshot.id, co_change.left_path, co_change.right_path),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                left_path=co_change.left_path,
                right_path=co_change.right_path,
                commit_count=co_change.commit_count,
                confidence=co_change.confidence,
                evidence_shas=list(co_change.evidence_shas),
                analysis_version=evolution.analysis_version,
            )
        )
    for hotspot in evolution.hotspots:
        db.add(
            FileHotspot(
                id=_stable_id("hot", snapshot.id, hotspot.path),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                path=hotspot.path,
                commit_count=hotspot.commit_count,
                churn=hotspot.churn,
                score=hotspot.score,
                evidence_shas=list(hotspot.evidence_shas),
            )
        )
    for module in evolution.modules:
        db.add(
            ModuleCandidate(
                id=_stable_id("mod", snapshot.id, module.key),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                natural_key=module.key,
                file_paths=list(module.files),
                confidence=module.confidence,
                description=module.description,
                evidence_shas=list(module.evidence_shas),
                inferred=module.inferred,
                analysis_version=evolution.analysis_version,
            )
        )


def _normalize_change_times(
    db: Session,
    repository: Repository,
    snapshot: RepositorySnapshot,
    evolution: EvolutionResult,
) -> None:
    """Correct file-change times stored before UTC normalization (idempotent)."""
    instants = {commit.sha: _as_utc(commit.authored_at) for commit in evolution.commits}
    if not instants:
        return
    for change in db.scalars(
        select(FileChange).where(
            FileChange.workspace_id == repository.workspace_id,
            FileChange.snapshot_id == snapshot.id,
        )
    ):
        instant = instants.get(change.commit_sha)
        if instant is not None and _as_utc(change.authored_at) != instant:
            change.authored_at = instant


def _write_graph_rows(
    db: Session,
    run: AnalysisRun,
    repository: Repository,
    snapshot: RepositorySnapshot,
    graph: GenomeGraph,
) -> None:
    """Insert provenance, nodes, edges, and diagnostics for a snapshot's graph."""
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


def _persist_graph(
    db: Session,
    run: AnalysisRun,
    repository: Repository,
    source_snapshot: RepositorySourceSnapshot,
    graph: GenomeGraph,
    replace: RepositorySnapshot | None = None,
) -> RepositorySnapshot:
    """Write a snapshot's structural graph.

    ``replace`` is a published snapshot of the same commit built by an older analyzer. Its
    graph is replaced inside the caller's transaction, so readers see the old graph or the
    new one, never a partial mix. Knowledge chunks and bug links are kept.
    """
    if replace is not None:
        snapshot = replace
        db.execute(delete(GraphEdge).where(GraphEdge.snapshot_id == snapshot.id))
        db.execute(delete(ParseDiagnostic).where(ParseDiagnostic.snapshot_id == snapshot.id))
        db.execute(delete(GraphNode).where(GraphNode.snapshot_id == snapshot.id))
        db.execute(
            delete(Provenance).where(
                Provenance.snapshot_id == snapshot.id,
                Provenance.kind.in_(GRAPH_EVIDENCE_KINDS),
            )
        )
        snapshot.run_id = run.id
        snapshot.analysis_version = graph.analysis_version
        snapshot.tree_sha = source_snapshot.tree_sha
        db.flush()
        _persist_manifest(db, repository, snapshot, source_snapshot)
        _write_graph_rows(db, run, repository, snapshot, graph)
        snapshot.published_at = utc_now()
        return snapshot
    snapshot = RepositorySnapshot(
        id=_stable_id("snap", run.workspace_id, repository.id, graph.repository_sha),
        workspace_id=run.workspace_id,
        repository_id=repository.id,
        commit_sha=graph.repository_sha,
        tree_sha=source_snapshot.tree_sha,
        run_id=run.id,
        analysis_version=graph.analysis_version,
        published_at=None,
    )
    db.add(snapshot)
    db.flush()
    _persist_manifest(db, repository, snapshot, source_snapshot)

    _write_graph_rows(db, run, repository, snapshot, graph)
    snapshot.published_at = utc_now()
    return snapshot


def _knowledge_files(
    git_repository: GitRepository, source_snapshot: RepositorySourceSnapshot
) -> list[tuple[RepositorySourceFile, ChunkKind]]:
    """Pick docs, manifests, and text source worth citing, most important first."""
    chosen: list[tuple[RepositoryManifestFile, ChunkKind]] = []
    for entry in source_snapshot.manifest:
        kind = knowledge.classify(entry.path)
        if kind is not None and entry.mode != "120000" and entry.size <= knowledge.MAX_FILE_BYTES:
            chosen.append((entry, kind))
    chosen.sort(key=lambda item: knowledge.priority(item[0].path, item[1]))
    chosen = chosen[: knowledge.MAX_FILES]
    kinds = {entry.path: kind for entry, kind in chosen}
    already_read = {item.path: item for item in source_snapshot.files if item.path in kinds}
    budget = knowledge.MAX_TOTAL_BYTES - sum(item.size for item in already_read.values())
    fetched = git_repository.read_blobs(
        [entry for entry, _ in chosen if entry.path not in already_read],
        max_file_bytes=knowledge.MAX_FILE_BYTES,
        max_total_bytes=max(1, budget),
    )
    files = {**already_read, **{item.path: item for item in fetched}}
    return sorted(
        ((files[path], kinds[path]) for path in files),
        key=lambda item: knowledge.priority(item[0].path, item[1]),
    )


def _persist_knowledge(
    db: Session,
    repository: Repository,
    snapshot: RepositorySnapshot,
    files: list[tuple[RepositorySourceFile, ChunkKind]],
) -> int:
    """Store cited chunks with provenance. Idempotent per snapshot."""
    existing = db.scalar(
        select(KnowledgeChunkRecord.id).where(KnowledgeChunkRecord.snapshot_id == snapshot.id)
    )
    if existing is not None:
        return 0
    stored = 0
    for source_file, kind in files:
        for chunk in knowledge.chunk_file(source_file.path, kind, source_file.content):
            if stored >= knowledge.MAX_CHUNKS:
                return stored
            provenance_id = _stable_id(
                "ev", repository.id, snapshot.commit_sha, chunk.path, chunk.ordinal, "knowledge"
            )
            db.add(
                Provenance(
                    id=provenance_id,
                    workspace_id=repository.workspace_id,
                    repository_id=repository.id,
                    snapshot_id=snapshot.id,
                    kind="knowledge",
                    repository_sha=snapshot.commit_sha,
                    file_path=chunk.path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    extractor_version=knowledge.KNOWLEDGE_VERSION,
                )
            )
            db.flush()
            db.add(
                KnowledgeChunkRecord(
                    id=_stable_id("kch", snapshot.id, chunk.path, chunk.ordinal),
                    workspace_id=repository.workspace_id,
                    repository_id=repository.id,
                    snapshot_id=snapshot.id,
                    provenance_id=provenance_id,
                    path=chunk.path,
                    blob_sha=source_file.blob_sha,
                    kind=chunk.kind,
                    ordinal=chunk.ordinal,
                    heading=chunk.heading,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    text=chunk.text,
                    analysis_version=knowledge.KNOWLEDGE_VERSION,
                )
            )
            stored += 1
    return stored


def _trace_bugs(
    git_repository: GitRepository, commits: tuple[GitCommit, ...], evolution: EvolutionResult
) -> SzzResult:
    """Run bounded SZZ-lite. Best effort: a failure yields no links, never a failed run."""
    sizes = {item.sha: (len(item.files), item.churn) for item in evolution.commits}
    try:
        return trace_bug_introductions(
            git_repository,
            commits,
            max_fix_commits=SZZ_MAX_FIX_COMMITS,
            commit_sizes=sizes,
        )
    except Exception:  # noqa: BLE001 - bug tracing must not break publication
        logger.exception("szz_tracing_failed")
        return SzzResult(
            links=(),
            fix_shas=(),
            examined_fixes=0,
            limitations=("Bug tracing failed for this snapshot; no bug links were stored.",),
        )


def _persist_bug_links(
    db: Session, repository: Repository, snapshot: RepositorySnapshot, result: SzzResult
) -> int:
    """Store SZZ-lite candidate links with provenance. Idempotent per snapshot."""
    existing = db.scalar(select(BugLink.id).where(BugLink.snapshot_id == snapshot.id).limit(1))
    if existing is not None or not result.links:
        return 0
    stored = 0
    for link in result.links:
        first = link.blamed_ranges[0][0] if link.blamed_ranges else None
        last = link.blamed_ranges[-1][1] if link.blamed_ranges else None
        provenance_id = _stable_id(
            "ev", snapshot.id, link.fix_sha, link.path, link.introducing_sha, "szz"
        )
        if db.get(Provenance, provenance_id) is None:
            db.add(
                Provenance(
                    id=provenance_id,
                    workspace_id=repository.workspace_id,
                    repository_id=repository.id,
                    snapshot_id=snapshot.id,
                    kind="szz_blame",
                    repository_sha=link.parent_sha,
                    file_path=link.path,
                    start_line=first,
                    end_line=last,
                    extractor_version=result.analysis_version,
                )
            )
            db.flush()
        db.add(
            BugLink(
                id=_stable_id("bug", snapshot.id, link.fix_sha, link.introducing_sha, link.path),
                workspace_id=repository.workspace_id,
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                provenance_id=provenance_id,
                fix_sha=link.fix_sha,
                introducing_sha=link.introducing_sha,
                path=link.path,
                lines=link.lines,
                confidence=link.confidence,
                evidence_json={
                    "fix_sha": link.fix_sha,
                    "parent_sha": link.parent_sha,
                    "path": link.path,
                    "fix_removed_ranges": [list(item) for item in link.fix_ranges],
                    "blamed_ranges": [list(item) for item in link.blamed_ranges],
                    "bulk_introducing_commit": link.bulk,
                    "shallow_boundary": link.boundary,
                },
                analysis_version=result.analysis_version,
            )
        )
        stored += 1
    return stored


def _train_models(
    session_factory: SessionFactory, workspace_id: str, repository_id: str, commit_sha: str
) -> None:
    """Train the snapshot's ML models. Best effort: a failure never fails the analysis."""
    try:
        with session_factory() as db:
            snapshot = _published_snapshot(db, workspace_id, repository_id, commit_sha)
            if snapshot is None:
                return
            train_snapshot_models(db, snapshot, "system:worker")
            db.commit()
    except Exception:  # noqa: BLE001 - model training must not break publication
        logger.exception(
            "ml_training_failed",
            extra={"repository_id": repository_id, "commit_sha": commit_sha},
        )


def run_structural_analysis(
    run_id: str,
    session_factory: SessionFactory = SessionLocal,
    sync_repository: SyncRepository = sync_github_repository,
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
        workspace_id = repository.workspace_id
        repository_id = repository.id
        branch = run.requested_refs[0] if run.requested_refs else repository.default_branch
        connection = db.scalar(
            select(RepositoryConnection).where(
                RepositoryConnection.repository_id == repository.id,
                RepositoryConnection.workspace_id == repository.workspace_id,
                RepositoryConnection.revoked_at.is_(None),
            )
        )

    _set_progress(
        session_factory, run_id, 0.05, "fetching", message="Fetching the repository mirror."
    )
    try:
        credential = _load_git_credential(
            connection, workspace_id=workspace_id, repository_id=repository_id
        )
        git_repository = sync_repository(
            clone_url,
            _mirror_path(settings.mirror_root, workspace_id, repository_id),
            branch,
            depth=settings.clone_depth,
            timeout_seconds=settings.clone_timeout_seconds,
            credential=credential,
        )
        source_snapshot = git_repository.read_source_snapshot(
            branch,
            max_files=settings.max_source_files,
            max_manifest_files=settings.max_manifest_files,
            max_file_bytes=settings.max_source_file_bytes,
            max_total_bytes=settings.max_source_total_bytes,
        )
        _set_progress(
            session_factory,
            run_id,
            0.15,
            "indexing",
            {
                "files_indexed": len(source_snapshot.manifest),
                "source_files": len(source_snapshot.files),
            },
            f"Indexed {len(source_snapshot.manifest)} files "
            f"({len(source_snapshot.files)} JS/TS source files).",
        )
        refs = git_repository.list_branch_refs()
        commits = git_repository.read_commit_history(
            branch, max_commits=settings.max_history_commits
        )
        _set_progress(
            session_factory,
            run_id,
            0.2,
            "mining_history",
            {"commits_mined": len(commits)},
            f"Mined {len(commits)} commits and {len(refs)} branches.",
        )
        evolution = analyze_evolution(
            mine_commit_changes(git_repository.git_directory, tuple(item.sha for item in commits)),
            tuple(item.path for item in source_snapshot.files),
        )
        _set_progress(
            session_factory,
            run_id,
            0.3,
            "evolution",
            {
                "co_change_pairs": len(evolution.co_changes),
                "modules_discovered": len(evolution.modules),
            },
            f"Discovered {len(evolution.modules)} modules and "
            f"{len(evolution.co_changes)} co-change pairs.",
        )
        knowledge_files = _knowledge_files(git_repository, source_snapshot)
        with session_factory() as db:
            reused = _published_snapshot(
                db, workspace_id, repository_id, source_snapshot.commit_sha
            )
            already_traced = reused is not None and (
                db.scalar(select(BugLink.id).where(BugLink.snapshot_id == reused.id).limit(1))
                is not None
            )
        if already_traced:
            szz = SzzResult(links=(), fix_shas=(), examined_fixes=0)
        else:
            _set_progress(
                session_factory,
                run_id,
                0.35,
                "tracing_bugs",
                message="Tracing bug-fix commits back to candidate introducing commits.",
            )
            szz = _trace_bugs(git_repository, commits, evolution)
            _set_progress(
                session_factory,
                run_id,
                0.4,
                "tracing_bugs",
                {"fix_commits": len(szz.fix_shas), "bug_links_traced": len(szz.links)},
                f"Traced {len(szz.links)} candidate bug links from {len(szz.fix_shas)} "
                "fix commits.",
            )

        with session_factory() as db:
            current_repository = db.scalar(
                select(Repository).where(
                    Repository.id == repository_id,
                    Repository.workspace_id == workspace_id,
                )
            )
            if current_repository is None:
                raise GitOperationError("Analysis scope disappeared before publication.")
            _persist_history(db, current_repository, refs, commits)
            record_audit_event(
                db,
                workspace_id=workspace_id,
                actor_id="system:worker",
                action="repository.evidence.fetched",
                resource_type="repository",
                resource_id=repository_id,
                after_hash=hashlib.sha256(source_snapshot.commit_sha.encode()).hexdigest(),
                request_id=run_id,
            )
            existing = _published_snapshot(
                db, workspace_id, repository_id, source_snapshot.commit_sha
            )
            stale = existing is not None and existing.analysis_version != CURRENT_GRAPH_VERSION
            if existing and not stale:
                _persist_manifest(db, current_repository, existing, source_snapshot)
                _persist_evolution(db, current_repository, existing, evolution)
                backfilled = _persist_knowledge(db, current_repository, existing, knowledge_files)
                bug_backfill = _persist_bug_links(db, current_repository, existing, szz)
                current_run = db.scalar(
                    select(AnalysisRun).where(
                        AnalysisRun.id == run_id,
                        AnalysisRun.workspace_id == workspace_id,
                    )
                )
                if current_run:
                    current_run.snapshot_sha = existing.commit_sha
                    current_run.version = existing.analysis_version
                    current_run.state = "SUCCEEDED"
                    current_run.stage = "complete"
                    current_run.progress = 1.0
                    current_run.completed_at = utc_now()
                    current_run.diagnostics = [
                        "Reused the existing immutable snapshot for this repository commit.",
                        *(
                            [f"Added {backfilled} project knowledge chunks to the snapshot."]
                            if backfilled
                            else []
                        ),
                        *(
                            [f"Added {bug_backfill} candidate bug links to the snapshot."]
                            if bug_backfill
                            else []
                        ),
                    ]
                    db.commit()
                return
            db.commit()

        _set_progress(
            session_factory,
            run_id,
            0.5,
            "parsing",
            message=f"Parsing {len(source_snapshot.files)} JS/TS source files.",
        )
        analyses: list[FileAnalysis] = [
            analyze_source(source_file.path, source_file.content)
            for source_file in source_snapshot.files
        ]
        graph = build_structural_graph(repository_id, source_snapshot.commit_sha, analyses)
        edge_counts = Counter(edge.kind for edge in graph.edges)
        _set_progress(
            session_factory,
            run_id,
            0.7,
            "publishing",
            {
                "files_parsed": len(analyses),
                "dependencies_mapped": edge_counts["IMPORTS"],
                "call_edges": edge_counts["CALLS"],
            },
            f"Parsed {len(analyses)} files; mapped {edge_counts['IMPORTS']} dependencies and "
            f"{edge_counts['CALLS']} candidate calls.",
        )

        with session_factory() as db:
            current_run = db.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.id == run_id,
                    AnalysisRun.workspace_id == workspace_id,
                )
            )
            current_repository = db.scalar(
                select(Repository).where(
                    Repository.id == repository_id,
                    Repository.workspace_id == workspace_id,
                )
            )
            if current_run is None or current_repository is None:
                raise GitOperationError("Analysis scope disappeared before publication.")
            snapshot = _persist_graph(
                db,
                current_run,
                current_repository,
                source_snapshot,
                graph,
                replace=_published_snapshot(
                    db, workspace_id, repository_id, source_snapshot.commit_sha
                ),
            )
            _persist_evolution(db, current_repository, snapshot, evolution)
            chunk_count = _persist_knowledge(db, current_repository, snapshot, knowledge_files)
            bug_count = _persist_bug_links(db, current_repository, snapshot, szz)
            current_run.snapshot_sha = source_snapshot.commit_sha
            current_run.version = graph.analysis_version
            current_run.state = "SUCCEEDED"
            current_run.stage = "complete"
            current_run.progress = 1.0
            current_run.completed_at = utc_now()
            current_run.progress_counts = {
                **(current_run.progress_counts or {}),
                "knowledge_chunks": chunk_count,
                "bug_links_traced": bug_count,
            }
            current_run.diagnostics = [
                f"Published {len(graph.nodes)} nodes and {len(graph.edges)} edges from "
                f"{len(analyses)} source files.",
                f"Recorded {len(graph.diagnostics)} structural diagnostics.",
                f"Recorded {len(source_snapshot.manifest)} files and {len(commits)} commits.",
                f"Derived {len(evolution.co_changes)} co-change edges and "
                f"{len(evolution.modules)} inferred modules.",
                f"Skipped {len(source_snapshot.skipped_oversized_files)} oversized source files.",
                (
                    f"Stored {chunk_count} cited knowledge chunks from {len(knowledge_files)} "
                    "docs, manifests, and source files."
                    if chunk_count
                    else "Kept the snapshot's existing knowledge chunks."
                ),
                f"Traced {bug_count} candidate bug-introducing links from "
                f"{len(szz.fix_shas)} fix commits ({szz.analysis_version}; heuristic).",
                *szz.limitations,
            ]
            db.commit()
        _train_models(session_factory, workspace_id, repository_id, source_snapshot.commit_sha)
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
    except (CredentialConfigurationError, CredentialDecryptionError):
        logger.warning("repository_credential_unavailable", extra={"analysis_run_id": run_id})
        _fail_run(
            session_factory,
            run_id,
            "CREDENTIAL_UNAVAILABLE",
            "The private repository credential could not be used.",
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
