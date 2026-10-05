"""Plain inputs for the ML models, decoupled from the database layer."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommitRecord:
    sha: str
    message: str
    author: str
    authored_at: datetime
    parent_count: int = 1


@dataclass(frozen=True)
class ChangeRecord:
    commit_sha: str
    path: str
    authored_at: datetime
    churn: int


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    symbols: tuple[str, ...] = ()
    evidence_id: str | None = None
    # Code metrics read from the FILE graph node when the analyzer recorded them.
    # ``None`` means "not measured", never zero.
    loc: int | None = None
    complexity: float | None = None
    functions: int | None = None


@dataclass(frozen=True)
class BugLinkRecord:
    """A candidate SZZ-lite link: ``introducing_sha`` last touched lines ``fix_sha`` removed."""

    path: str
    introducing_sha: str
    fix_sha: str
    confidence: float


@dataclass(frozen=True)
class ImportRecord:
    source: str
    target: str


@dataclass(frozen=True)
class SearchDocument:
    id: str
    kind: str
    text: str
    title: str
    path: str | None = None


def change_authors(
    commits: list[CommitRecord], changes: list[ChangeRecord]
) -> dict[str, dict[str, int]]:
    """Commits per author for every file, joining file changes to their commit's author."""
    author = {commit.sha: commit.author for commit in commits}
    seen: set[tuple[str, str]] = set()
    result: dict[str, dict[str, int]] = {}
    for change in changes:
        if (change.commit_sha, change.path) in seen or change.commit_sha not in author:
            continue
        seen.add((change.commit_sha, change.path))
        per_file = result.setdefault(change.path, {})
        name = author[change.commit_sha]
        per_file[name] = per_file.get(name, 0) + 1
    return result


def bulk_commit_shas(changes: list[ChangeRecord], file_count: int) -> set[str]:
    """Commits touching an unusually large share of the tree (formatting sweeps,
    vendoring, renames). They carry little signal about individual files and would
    otherwise dominate churn and co-change features, so models exclude them."""
    touched: dict[str, set[str]] = {}
    for change in changes:
        touched.setdefault(change.commit_sha, set()).add(change.path)
    limit = max(15, int(file_count * 0.2))
    return {sha for sha, paths in touched.items() if len(paths) > limit}
