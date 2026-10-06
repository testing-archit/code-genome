"""Reviewer suggestions and knowledge concentration from commit history (``ownership@1``).

* Reviewers for a change: people who changed the affected files, each commit weighted by
  recency (half-life ``HALF_LIFE_DAYS``), so someone active last month outranks someone who left
  two years ago. Bots and bulk commits (reformatting, initial imports) are excluded.
* Bus factor of a component: the smallest number of people who together made at least half of
  its commits. A bus factor of 1 on an active component means one person holds most of the
  history; it is a knowledge-concentration signal, not a judgement about anyone.

Author identities are Git author emails as recorded; they are not verified.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from code_genome_ml.records import ChangeRecord, bulk_commit_shas
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import FileChange, FileManifestEntry, RepositoryCommit, RepositorySnapshot

OWNERSHIP_VERSION = "ownership@1"
HALF_LIFE_DAYS = 180.0
MIN_COMPONENT_COMMITS = 10
_BOT_MARKERS = ("[bot]", "bot@", "-bot@", "dependabot", "renovate", "github-actions")


def is_bot(name: str, email: str) -> bool:
    text = f"{name} {email}".lower()
    return any(marker in text for marker in _BOT_MARKERS)


@dataclass
class Reviewer:
    name: str
    email: str
    score: float = 0.0
    commits: int = 0
    files: set[str] = field(default_factory=set)
    last_commit: datetime | None = None
    evidence_shas: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class History:
    """Per-snapshot commit authorship, loaded once."""

    authors: dict[str, tuple[str, str]]  # sha -> (name, email)
    when: dict[str, datetime]
    files_by_commit: dict[str, set[str]]
    newest: datetime | None


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def load_history(db: Session, snapshot: RepositorySnapshot) -> History:
    commits = {
        row.sha: row
        for row in db.scalars(
            select(RepositoryCommit).where(
                RepositoryCommit.repository_id == snapshot.repository_id,
                RepositoryCommit.workspace_id == snapshot.workspace_id,
            )
        )
        if len(row.parent_shas or []) <= 1 and not is_bot(row.author_name, row.author_email)
    }
    changes = [
        row
        for row in db.scalars(
            select(FileChange).where(
                FileChange.snapshot_id == snapshot.id,
                FileChange.workspace_id == snapshot.workspace_id,
            )
        )
        if row.commit_sha in commits
    ]
    files_total = (
        db.scalar(
            select(func.count())
            .select_from(FileManifestEntry)
            .where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == snapshot.workspace_id,
            )
        )
        or 0
    )
    bulk = bulk_commit_shas(
        [ChangeRecord(row.commit_sha, row.path, row.authored_at, row.churn) for row in changes],
        files_total,
    )
    files_by_commit: dict[str, set[str]] = defaultdict(set)
    for row in changes:
        if row.commit_sha not in bulk:
            files_by_commit[row.commit_sha].add(row.path)
    when = {sha: _utc(commits[sha].authored_at) for sha in files_by_commit}
    return History(
        authors={
            sha: (commits[sha].author_name, commits[sha].author_email.lower())
            for sha in files_by_commit
        },
        when=when,
        files_by_commit=dict(files_by_commit),
        newest=max(when.values(), default=None),
    )


def suggest_reviewers(history: History, paths: Iterable[str], limit: int = 3) -> list[Reviewer]:
    """People who changed ``paths``, ranked by recency-weighted commits."""
    wanted = set(paths)
    reviewers: dict[str, Reviewer] = {}
    if history.newest is None or not wanted:
        return []
    for sha, touched in sorted(
        history.files_by_commit.items(), key=lambda item: history.when[item[0]], reverse=True
    ):
        overlap = touched & wanted
        if not overlap:
            continue
        name, email = history.authors[sha]
        person = reviewers.setdefault(email, Reviewer(name=name, email=email))
        age_days = max(0.0, (history.newest - history.when[sha]).total_seconds() / 86_400)
        person.score += math.pow(0.5, age_days / HALF_LIFE_DAYS)
        person.commits += 1
        person.files |= overlap
        person.last_commit = max(filter(None, (person.last_commit, history.when[sha])))
        if len(person.evidence_shas) < 3:
            person.evidence_shas.append(sha)
    ranked = sorted(
        reviewers.values(), key=lambda item: (-item.score, -len(item.files), item.email)
    )
    return ranked[:limit]


def bus_factor(history: History, paths: Iterable[str]) -> tuple[int, list[tuple[str, int]]] | None:
    """(smallest number of authors covering >= 50% of commits, top authors) or None if quiet."""
    wanted = set(paths)
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for sha, touched in history.files_by_commit.items():
        if touched & wanted:
            name, email = history.authors[sha]
            counts[email] += 1
            names[email] = name
    total = sum(counts.values())
    if total < MIN_COMPONENT_COMMITS:
        return None
    covered = 0
    factor = 0
    for _, count in counts.most_common():
        covered += count
        factor += 1
        if covered * 2 >= total:
            break
    return factor, [(names[email], count) for email, count in counts.most_common(3)]
