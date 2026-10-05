"""Deterministic parsing of untrusted unified diffs into repository-relative file changes."""

import re
from dataclasses import dataclass
from typing import Literal

ChangeKind = Literal["added", "modified", "deleted", "renamed", "listed"]

MAX_DIFF_FILES = 200
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_GIT_HEADER = re.compile(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$')


class DiffError(ValueError):
    """The diff or a path in it cannot be accepted."""


@dataclass
class FileDiff:
    path: str
    change: ChangeKind
    previous_path: str | None = None
    additions: int = 0
    deletions: int = 0


def normalize_repository_path(value: str) -> str:
    """Return a clean repository-relative path, rejecting traversal and control characters."""
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or len(path) > 1000:
        raise DiffError("File paths must be between 1 and 1000 characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise DiffError("File paths must not contain control characters.")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise DiffError("File paths must be repository-relative.")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise DiffError("File paths must not contain empty, '.', or '..' segments.")
    return path


def _side(value: str) -> str | None:
    value = value.split("\t", 1)[0].strip().strip('"')
    if value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return normalize_repository_path(value)


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse `git diff` / unified diff text. Raises DiffError on unsafe or unparseable input.

    Hunk bodies are consumed by the line counts in their `@@` headers, so content lines that
    happen to begin with `---`, `+++`, or `diff --git` are never mistaken for file headers.
    """
    files: list[FileDiff] = []
    current: FileDiff | None = None
    old_path: str | None = None
    awaiting_new_side = False
    old_left = new_left = 0

    def start(path: str) -> FileDiff:
        if len(files) >= MAX_DIFF_FILES:
            raise DiffError(f"Diffs are limited to {MAX_DIFF_FILES} files.")
        item = FileDiff(path=path, change="modified")
        files.append(item)
        return item

    for line in text.splitlines():
        if (old_left > 0 or new_left > 0) and line and line[0] not in " +-\\":
            # Hunk counts were wrong (common in hand-edited pastes); a line without a
            # content prefix cannot belong to the hunk, so let it be read as a header.
            old_left = new_left = 0
        if (old_left > 0 or new_left > 0) and current is not None:
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                current.additions += 1
                new_left -= 1
            elif line.startswith("-"):
                current.deletions += 1
                old_left -= 1
            else:
                old_left -= 1
                new_left -= 1
            continue
        header = _GIT_HEADER.match(line)
        if header:
            old_path = normalize_repository_path(header.group(1))
            current = start(normalize_repository_path(header.group(2)))
            awaiting_new_side = True
            continue
        if line.startswith("--- "):
            old_path = _side(line[4:])
            continue
        if line.startswith("+++ "):
            new_path = _side(line[4:])
            if not awaiting_new_side:
                target = new_path or old_path
                if target is None:
                    raise DiffError("A diff file header names /dev/null on both sides.")
                current = start(target)
            awaiting_new_side = False
            if current is not None:
                if new_path is None:
                    current.change = "deleted"
                    current.path = old_path or current.path
                elif old_path is None:
                    current.change = "added"
            continue
        if current is None:
            continue
        hunk = _HUNK.match(line)
        if hunk:
            old_left = int(hunk.group(1) or 1)
            new_left = int(hunk.group(2) or 1)
        elif line.startswith("new file mode"):
            current.change = "added"
        elif line.startswith("deleted file mode"):
            current.change = "deleted"
        elif line.startswith("rename from "):
            current.previous_path = normalize_repository_path(line[len("rename from ") :])
            current.change = "renamed"
        elif line.startswith("rename to "):
            current.path = normalize_repository_path(line[len("rename to ") :])
            current.change = "renamed"

    merged: dict[str, FileDiff] = {}
    for item in files:
        existing = merged.get(item.path)
        if existing is None:
            merged[item.path] = item
        else:
            existing.additions += item.additions
            existing.deletions += item.deletions
    if not merged:
        raise DiffError("No file changes were found. Paste the output of `git diff`.")
    return list(merged.values())
