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


def bulk_commit_shas(changes: list[ChangeRecord], file_count: int) -> set[str]:
    """Commits touching an unusually large share of the tree (formatting sweeps,
    vendoring, renames). They carry little signal about individual files and would
    otherwise dominate churn and co-change features, so models exclude them."""
    touched: dict[str, set[str]] = {}
    for change in changes:
        touched.setdefault(change.commit_sha, set()).add(change.path)
    limit = max(15, int(file_count * 0.2))
    return {sha for sha, paths in touched.items() if len(paths) > limit}
