"""A short, deterministic orientation brief about a repository snapshot.

Built only from stored evidence (README excerpt, package manifest, file inventory, inferred
modules, and commit history) so a voice agent knows what "this project" refers to before
it searches. It is background, not a citation: specific answers still come from the
evidence tool.
"""

from collections import Counter
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FileManifestEntry,
    KnowledgeChunkRecord,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)
from .question_routing import PROJECT_MANIFESTS

MAX_BRIEF_CHARS = 2_000


def _plain(text: str, limit: int) -> str:
    lines = [
        line.strip("#>*-` ").strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("![", "<", "[!", "```", "|"))
    ]
    collapsed = " ".join(line for line in lines if line)
    return collapsed[:limit].rsplit(" ", 1)[0] + "…" if len(collapsed) > limit else collapsed


def repository_brief(db: Session, snapshot: RepositorySnapshot, repository_name: str) -> str:
    scope = (
        KnowledgeChunkRecord.snapshot_id == snapshot.id,
        KnowledgeChunkRecord.workspace_id == snapshot.workspace_id,
    )
    readme = db.scalar(
        select(KnowledgeChunkRecord)
        .where(*scope, KnowledgeChunkRecord.kind == "readme")
        .order_by(
            func.length(KnowledgeChunkRecord.path),
            KnowledgeChunkRecord.path,
            KnowledgeChunkRecord.ordinal,
        )
        .limit(1)
    )
    manifests = [
        chunk
        for chunk in db.scalars(
            select(KnowledgeChunkRecord)
            .where(*scope, KnowledgeChunkRecord.kind == "manifest")
            .order_by(func.length(KnowledgeChunkRecord.path), KnowledgeChunkRecord.path)
        )
        if PurePosixPath(chunk.path).name.lower() in PROJECT_MANIFESTS
    ][:3]
    paths = list(
        db.scalars(
            select(FileManifestEntry.path).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == snapshot.workspace_id,
            )
        )
    )
    folders = Counter(path.split("/", 1)[0] for path in paths if "/" in path)
    suffixes = Counter(
        PurePosixPath(path).suffix.lower() for path in paths if PurePosixPath(path).suffix
    )
    modules = [
        module.natural_key
        for module in sorted(
            db.scalars(
                select(ModuleCandidate).where(
                    ModuleCandidate.snapshot_id == snapshot.id,
                    ModuleCandidate.workspace_id == snapshot.workspace_id,
                )
            ),
            key=lambda module: (-len(module.file_paths), module.natural_key),
        )[:8]
    ]
    latest = db.scalar(
        select(RepositoryCommit)
        .where(
            RepositoryCommit.repository_id == snapshot.repository_id,
            RepositoryCommit.workspace_id == snapshot.workspace_id,
        )
        .order_by(RepositoryCommit.authored_at.desc())
        .limit(1)
    )

    parts = [f"Repository {repository_name} at commit {snapshot.commit_sha[:12]}."]
    if readme is not None:
        parts.append(f"README ({readme.path}) says: {_plain(readme.text, 600)}")
    parts.extend(_plain(manifest.text, 260) for manifest in manifests)
    if paths:
        parts.append(
            f"{len(paths)} files. Top-level folders: "
            + ", ".join(f"{name} ({count})" for name, count in folders.most_common(8))
            + ". File types: "
            + ", ".join(f"{suffix} ({count})" for suffix, count in suffixes.most_common(6))
            + "."
        )
    if modules:
        parts.append("Inferred modules: " + ", ".join(modules) + ".")
    if latest is not None:
        subject = latest.message.splitlines()[0][:120] if latest.message else ""
        parts.append(f"Latest commit {latest.authored_at.date().isoformat()}: {subject}")
    brief = " ".join(parts)
    return brief[:MAX_BRIEF_CHARS]
