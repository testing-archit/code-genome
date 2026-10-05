"""Simplified SZZ: trace lines removed by bug-fix commits back to candidate introducing commits.

Algorithm (``szz-lite@1``):

1. Classify fix commits deterministically from the commit subject with a word-bounded keyword
   rule (fix, bug, regression, hotfix, patch, crash, error and their inflections). Merge
   commits are never treated as fixes.
2. For each of the most recent fixes, take the lines the fix deleted or modified
   (``git diff -U0 --ignore-all-space parent fix``), skipping lock/minified/generated files,
   blank and comment-only lines, and files whose removed-line count is too large to be a
   targeted fix.
3. ``git blame -w`` those line ranges on the parent commit. Each commit that last touched a
   removed line is a *candidate* bug-introducing commit. Merge commits are ignored.

The result is heuristic evidence, never proof: refactors, moved code, and fixes that only add
lines all distort it. Confidence is lowered for bulk commits (likely reformatting or initial
imports) and for commits at the edge of a shallow mirror's history.
"""

import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .repository import GitOperationError, GitRepository, RepositoryCommit

SZZ_VERSION = "szz-lite@1"
FIX_PATTERN = re.compile(
    r"\b(fix|fixes|fixed|fixing|bug|bugs|bugfix|bugfixes|regression|regressions|hotfix|"
    r"hotfixes|patch|patched|patches|crash|crashes|crashed|crashing|error|errors)\b",
    re.IGNORECASE,
)
_COMMENT_ONLY = re.compile(r"^\s*(//|/\*|\*/|\*|#|<!--|-->)")
_SKIPPED_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "composer.lock",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "Gemfile.lock",
    "go.sum",
}
_SKIPPED_SUFFIXES = (".min.js", ".min.css", ".map", ".snap", ".lock", ".svg")
_SKIPPED_DIRECTORIES = {"node_modules", "dist", "build", "vendor", ".next", "coverage", "out"}

BASE_CONFIDENCE = 0.7
BULK_CONFIDENCE = 0.35
BOUNDARY_CONFIDENCE = 0.25
BULK_FILES = 100
BULK_LINES = 2_000


def is_fix_message(message: str) -> bool:
    """Deterministic bug-fix rule over the commit subject line."""
    subject = message.strip().splitlines()[0] if message.strip() else ""
    return bool(FIX_PATTERN.search(subject))


def _skipped_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        pure.name in _SKIPPED_NAMES
        or path.endswith(_SKIPPED_SUFFIXES)
        or any(part in _SKIPPED_DIRECTORIES for part in pure.parts[:-1])
    )


def _ranges(lines: Sequence[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(lines))
    ranges: list[tuple[int, int]] = []
    for line in ordered:
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], line)
        else:
            ranges.append((line, line))
    return ranges


@dataclass(frozen=True)
class BugLinkCandidate:
    fix_sha: str
    parent_sha: str
    introducing_sha: str
    path: str
    lines: int
    blamed_ranges: tuple[tuple[int, int], ...]
    fix_ranges: tuple[tuple[int, int], ...]
    confidence: float
    boundary: bool
    bulk: bool


@dataclass(frozen=True)
class SzzResult:
    links: tuple[BugLinkCandidate, ...]
    fix_shas: tuple[str, ...]
    examined_fixes: int
    limitations: tuple[str, ...] = field(default_factory=tuple)
    analysis_version: str = SZZ_VERSION


def trace_bug_introductions(
    repository: GitRepository,
    commits: Sequence[RepositoryCommit],
    *,
    max_fix_commits: int = 60,
    max_files_per_fix: int = 20,
    max_removed_lines_per_file: int = 200,
    max_blames: int = 400,
    time_budget_seconds: float = 90.0,
    commit_sizes: Mapping[str, tuple[int, int]] | None = None,
) -> SzzResult:
    """Run bounded SZZ over the most recent fix commits in ``commits`` (newest first)."""
    started = time.monotonic()
    known = {commit.sha: commit for commit in commits}
    fixes = [
        commit
        for commit in commits
        if len(commit.parent_shas) == 1 and is_fix_message(commit.message)
    ][:max_fix_commits]
    sizes: dict[str, tuple[int, int]] = dict(commit_sizes or {})
    parents: dict[str, tuple[str, ...]] = {sha: item.parent_shas for sha, item in known.items()}
    limitations: list[str] = []
    links: list[BugLinkCandidate] = []
    blames = 0
    examined = 0
    skipped_fixes = 0

    def parent_count(sha: str) -> int:
        if sha not in parents:
            try:
                parents[sha] = repository.commit_parents(sha)
            except GitOperationError:
                parents[sha] = ()
        return len(parents[sha])

    def size(sha: str) -> tuple[int, int]:
        if sha not in sizes:
            try:
                sizes[sha] = repository.commit_size(sha)
            except GitOperationError:
                sizes[sha] = (0, 0)
        return sizes[sha]

    for fix in fixes:
        if time.monotonic() - started > time_budget_seconds or blames >= max_blames:
            limitations.append(
                f"Stopped after {examined} of {len(fixes)} fix commits (time or blame budget)."
            )
            break
        parent_sha = fix.parent_shas[0]
        try:
            removed = repository.removed_lines(parent_sha, fix.sha)
        except GitOperationError:
            # Parent outside a shallow mirror, or the diff exceeded its byte limit.
            skipped_fixes += 1
            continue
        examined += 1
        for path in sorted(removed)[:max_files_per_fix]:
            if _skipped_path(path) or blames >= max_blames:
                continue
            meaningful = [
                (number, content)
                for number, content in removed[path]
                if content.strip() and not _COMMENT_ONLY.match(content)
            ]
            if not meaningful or len(meaningful) > max_removed_lines_per_file:
                continue
            fix_ranges = _ranges([number for number, _ in meaningful])
            try:
                attributed = repository.blame_lines(parent_sha, path, fix_ranges)
            except (GitOperationError, ValueError):
                continue
            blames += 1
            by_commit: dict[str, list[int]] = defaultdict(list)
            boundary_shas: set[str] = set()
            for number, sha, boundary in attributed:
                by_commit[sha].append(number)
                if boundary:
                    boundary_shas.add(sha)
            for sha, numbers in sorted(by_commit.items()):
                if sha == fix.sha or parent_count(sha) > 1:
                    continue
                files_touched, lines_touched = size(sha)
                bulk = files_touched > BULK_FILES or lines_touched > BULK_LINES
                boundary = sha in boundary_shas
                confidence = BASE_CONFIDENCE
                if bulk:
                    confidence = BULK_CONFIDENCE
                if boundary:
                    confidence = min(confidence, BOUNDARY_CONFIDENCE)
                links.append(
                    BugLinkCandidate(
                        fix_sha=fix.sha,
                        parent_sha=parent_sha,
                        introducing_sha=sha,
                        path=path,
                        lines=len(numbers),
                        blamed_ranges=tuple(_ranges(numbers)),
                        fix_ranges=tuple(fix_ranges),
                        confidence=confidence,
                        boundary=boundary,
                        bulk=bulk,
                    )
                )
    if skipped_fixes:
        limitations.append(
            f"{skipped_fixes} fix commits could not be diffed (parent outside the fetched "
            "history or diff too large)."
        )
    return SzzResult(
        links=tuple(links),
        fix_shas=tuple(fix.sha for fix in fixes),
        examined_fixes=examined,
        limitations=tuple(limitations),
    )
