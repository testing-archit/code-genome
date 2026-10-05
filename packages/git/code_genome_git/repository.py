import fcntl
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

SUPPORTED_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
BRANCH_PATTERN = re.compile(r"^(?!/|.*(?:\.\.|//|@\{|\\|\s))[^~^:?*\[]+(?<![/.])$")
OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}(?:[0-9a-f]{24})?) (\d+) (\d+)(?: \d+)?$")


class GitOperationError(RuntimeError):
    pass


class RepositoryLimitError(GitOperationError):
    pass


@dataclass(frozen=True)
class GitCredential:
    token: str = field(repr=False)
    username: str = "x-access-token"

    def __post_init__(self) -> None:
        if (
            len(self.token) < 8
            or len(self.token) > 1024
            or any(character in self.token for character in "\x00\r\n")
        ):
            raise ValueError("Git credential token is invalid")
        if self.username != "x-access-token":
            raise ValueError("Only the fixed GitHub token username is allowed")


@dataclass(frozen=True)
class RepositoryBranchRef:
    name: str
    head_sha: str


@dataclass(frozen=True)
class RepositoryCommit:
    sha: str
    parent_shas: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: str
    message: str


@dataclass(frozen=True)
class RepositoryManifestFile:
    path: str
    blob_sha: str
    mode: str
    size: int


@dataclass(frozen=True)
class RepositorySourceFile:
    path: str
    blob_sha: str
    mode: str
    size: int
    content: bytes


@dataclass(frozen=True)
class RepositorySourceSnapshot:
    commit_sha: str
    tree_sha: str
    ref: str
    manifest: tuple[RepositoryManifestFile, ...]
    files: tuple[RepositorySourceFile, ...]
    skipped_oversized_files: tuple[str, ...]


def normalize_github_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Repository URL must be a credential-free HTTPS GitHub URL")
    parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise ValueError("Repository URL must identify one GitHub owner and repository")
    return f"https://github.com/{parts[0]}/{parts[1]}.git"


def validate_ref(value: str) -> str:
    if len(value) > 255 or not BRANCH_PATTERN.fullmatch(value):
        raise ValueError("Invalid Git ref name")
    return value


def _base_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "true",
            "LC_ALL": "C",
        }
    )
    for key in tuple(environment):
        if (
            key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
            or key.startswith("CODE_GENOME_GIT_")
        ):
            environment.pop(key)
    return environment


@contextmanager
def _git_environment(credential: GitCredential | None) -> Iterator[dict[str, str]]:
    environment = _base_git_environment()
    if credential is None:
        yield environment
        return
    with tempfile.TemporaryDirectory(prefix="code-genome-askpass-") as directory:
        helper = Path(directory) / "askpass.sh"
        helper.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *Username*) printf '%s\\n' \"$CODE_GENOME_GIT_USERNAME\" ;;\n"
            "  *) printf '%s\\n' \"$CODE_GENOME_GIT_PASSWORD\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        environment.update(
            {
                "GIT_ASKPASS": str(helper),
                "GIT_ASKPASS_REQUIRE": "force",
                "CODE_GENOME_GIT_USERNAME": credential.username,
                "CODE_GENOME_GIT_PASSWORD": credential.token,
            }
        )
        yield environment


def _safe_error(stderr: bytes, credential: GitCredential | None) -> str:
    text = stderr.decode("utf-8", errors="replace").strip().splitlines()
    detail = text[-1][:300] if text else "Git operation failed without diagnostic output."
    if credential:
        detail = detail.replace(credential.token, "[REDACTED]")
    return detail


def _run_git(
    arguments: list[str],
    *,
    timeout_seconds: int,
    max_output_bytes: int | None = None,
    credential: GitCredential | None = None,
) -> bytes:
    try:
        with _git_environment(credential) as environment:
            completed = subprocess.run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "protocol.file.allow=never",
                    *arguments,
                ],
                check=False,
                capture_output=True,
                env=environment,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as error:
        raise GitOperationError("Git operation exceeded its configured time limit.") from error
    except OSError as error:
        raise GitOperationError("Git executable could not be started.") from error
    if completed.returncode != 0:
        raise GitOperationError(_safe_error(completed.stderr, credential))
    if max_output_bytes is not None and len(completed.stdout) > max_output_bytes:
        raise RepositoryLimitError("Git output exceeded its configured byte limit.")
    return completed.stdout


def clone_github_repository(
    clone_url: str,
    destination: Path,
    branch: str,
    *,
    depth: int = 200,
    timeout_seconds: int = 120,
    credential: GitCredential | None = None,
) -> "GitRepository":
    normalized_url = normalize_github_url(clone_url)
    validated_branch = validate_ref(branch)
    if depth < 1 or depth > 10_000:
        raise ValueError("Clone depth must be between 1 and 10000")
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Clone destination must not already exist")
    if not destination.parent.is_dir():
        raise ValueError("Clone destination parent must exist")
    _run_git(
        [
            "clone",
            "--bare",
            "--no-tags",
            "--single-branch",
            "--branch",
            validated_branch,
            "--depth",
            str(depth),
            "--",
            normalized_url,
            str(destination),
        ],
        timeout_seconds=timeout_seconds,
        max_output_bytes=1_000_000,
        credential=credential,
    )
    destination.chmod(0o700)
    return GitRepository(destination, timeout_seconds=timeout_seconds)


def sync_github_repository(
    clone_url: str,
    destination: Path,
    branch: str,
    *,
    depth: int = 200,
    timeout_seconds: int = 120,
    credential: GitCredential | None = None,
) -> "GitRepository":
    """Create or incrementally fetch a locked, persistent bare GitHub mirror."""
    normalized_url = normalize_github_url(clone_url)
    validated_branch = validate_ref(branch)
    if depth < 1 or depth > 10_000:
        raise ValueError("Clone depth must be between 1 and 10000")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    lock_path = destination.with_name(f"{destination.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not destination.exists():
            with tempfile.TemporaryDirectory(
                prefix="code-genome-mirror-", dir=destination.parent
            ) as staging_root:
                staged = Path(staging_root) / "repository.git"
                repository = clone_github_repository(
                    normalized_url,
                    staged,
                    validated_branch,
                    depth=depth,
                    timeout_seconds=timeout_seconds,
                    credential=credential,
                )
                os.replace(repository.git_directory, destination)
            return GitRepository(destination, timeout_seconds=timeout_seconds)

        repository = GitRepository(destination, timeout_seconds=timeout_seconds)
        repository.assert_origin(normalized_url)
        repository._run(
            [
                "fetch",
                "--prune",
                "--no-tags",
                "--depth",
                str(depth),
                "origin",
                f"+refs/heads/{validated_branch}:refs/heads/{validated_branch}",
            ],
            max_output_bytes=1_000_000,
            credential=credential,
        )
        return repository
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class GitRepository:
    def __init__(self, git_directory: Path, *, timeout_seconds: int = 30) -> None:
        resolved = git_directory.resolve()
        if not resolved.is_dir():
            raise ValueError("Git directory does not exist")
        self.git_directory = resolved
        self.timeout_seconds = timeout_seconds

    def _run(
        self,
        arguments: list[str],
        *,
        max_output_bytes: int | None = None,
        credential: GitCredential | None = None,
    ) -> bytes:
        return _run_git(
            ["--git-dir", str(self.git_directory), *arguments],
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=max_output_bytes,
            credential=credential,
        )

    def assert_origin(self, expected_url: str) -> None:
        is_bare = self._run(["rev-parse", "--is-bare-repository"]).decode().strip()
        origin = self._run(["remote", "get-url", "origin"], max_output_bytes=2_000).decode().strip()
        if is_bare != "true" or origin != normalize_github_url(expected_url):
            raise GitOperationError("Existing mirror does not match the registered repository.")

    def resolve_ref(self, ref: str) -> str:
        """A branch name, or a full commit ID that must already be in the mirror."""
        if OBJECT_ID_PATTERN.fullmatch(ref):
            result = self._run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
            object_id = result.decode().strip().lower()
            if object_id != ref:
                raise GitOperationError("The commit is not in the fetched history.")
            return object_id
        validated = validate_ref(ref)
        result = self._run(["rev-parse", "--verify", f"refs/heads/{validated}^{{commit}}"])
        object_id = result.decode().strip().lower()
        if not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise GitOperationError("Git returned an invalid commit object ID.")
        return object_id

    def commit_before(self, branch: str, before: datetime) -> str | None:
        """The newest commit on ``branch`` committed at or before ``before`` (UTC), if fetched."""
        validated = validate_ref(branch)
        stamp = before.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        output = self._run(
            ["rev-list", "-1", f"--before={stamp}", f"refs/heads/{validated}"],
            max_output_bytes=200,
        )
        object_id = output.decode().strip().lower()
        if not object_id:
            return None
        if not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise GitOperationError("Git returned an invalid commit object ID.")
        return object_id

    def list_branch_refs(self, *, max_refs: int = 1_000) -> tuple[RepositoryBranchRef, ...]:
        if max_refs < 1:
            raise ValueError("Branch ref limit must be positive")
        output = self._run(
            ["for-each-ref", "--format=%(refname:strip=2)%00%(objectname)%00", "refs/heads"],
            max_output_bytes=max_refs * 400,
        )
        fields = output.split(b"\0")
        if fields and fields[-1] in {b"", b"\n"}:
            fields.pop()
        if len(fields) % 2:
            raise GitOperationError("Git returned malformed branch references.")
        refs: list[RepositoryBranchRef] = []
        for index in range(0, len(fields), 2):
            name = fields[index].decode("utf-8", errors="surrogateescape").strip()
            sha = fields[index + 1].decode("ascii", errors="strict").strip()
            validate_ref(name)
            if not OBJECT_ID_PATTERN.fullmatch(sha):
                raise GitOperationError("Git returned an invalid branch object ID.")
            refs.append(RepositoryBranchRef(name=name, head_sha=sha))
        if len(refs) > max_refs:
            raise RepositoryLimitError("Repository contains more branches than allowed.")
        return tuple(sorted(refs, key=lambda item: item.name))

    def read_commit_history(
        self, ref: str, *, max_commits: int = 200
    ) -> tuple[RepositoryCommit, ...]:
        if max_commits < 1 or max_commits > 10_000:
            raise ValueError("Commit history limit must be between 1 and 10000")
        commit_sha = self.resolve_ref(ref)
        output = self._run(
            [
                "log",
                f"--max-count={max_commits}",
                "--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%B%x00",
                commit_sha,
            ],
            max_output_bytes=max_commits * 20_000,
        )
        fields = output.split(b"\0")
        if fields and fields[-1] in {b"", b"\n"}:
            fields.pop()
        if len(fields) % 6:
            raise GitOperationError("Git returned malformed commit metadata.")
        commits: list[RepositoryCommit] = []
        for index in range(0, len(fields), 6):
            sha = fields[index].decode("ascii", errors="strict").strip()
            parent_shas = tuple(fields[index + 1].decode("ascii", errors="strict").split())
            if not OBJECT_ID_PATTERN.fullmatch(sha) or any(
                not OBJECT_ID_PATTERN.fullmatch(parent) for parent in parent_shas
            ):
                raise GitOperationError("Git returned an invalid commit object ID.")
            commits.append(
                RepositoryCommit(
                    sha=sha,
                    parent_shas=parent_shas,
                    author_name=fields[index + 2].decode("utf-8", errors="replace")[:500],
                    author_email=fields[index + 3].decode("utf-8", errors="replace")[:500],
                    authored_at=fields[index + 4].decode("ascii", errors="strict"),
                    message=fields[index + 5].decode("utf-8", errors="replace")[:10_000].strip(),
                )
            )
        return tuple(commits)

    def _object_id(self, value: str) -> str:
        if not OBJECT_ID_PATTERN.fullmatch(value):
            raise ValueError("Expected a full hexadecimal Git object ID")
        return value

    def commit_parents(self, commit_sha: str) -> tuple[str, ...]:
        output = self._run(
            ["rev-list", "--parents", "-n", "1", self._object_id(commit_sha), "--"],
            max_output_bytes=10_000,
        )
        parts = output.decode("ascii", errors="strict").split()
        if (
            not parts
            or parts[0] != commit_sha
            or not all(OBJECT_ID_PATTERN.fullmatch(item) for item in parts)
        ):
            raise GitOperationError("Git returned malformed parent metadata.")
        return tuple(parts[1:])

    def commit_size(self, commit_sha: str) -> tuple[int, int]:
        """Return (files touched, lines added + deleted) for a commit; binary files count 0."""
        output = self._run(
            [
                "diff-tree",
                "--root",
                "-r",
                "--no-commit-id",
                "--numstat",
                self._object_id(commit_sha),
            ],
            max_output_bytes=20_000_000,
        )
        files = 0
        lines = 0
        for record in output.decode("utf-8", errors="replace").splitlines():
            parts = record.split("\t", 2)
            if len(parts) != 3:
                continue
            files += 1
            lines += sum(int(part) for part in parts[:2] if part.isdigit())
        return files, lines

    def removed_lines(
        self, parent_sha: str, commit_sha: str, *, max_output_bytes: int = 4_000_000
    ) -> dict[str, list[tuple[int, str]]]:
        """Lines deleted or modified by ``commit_sha`` relative to ``parent_sha``.

        Keys are paths in the parent tree; values are (parent line number, content) pairs.
        Whitespace-only changes are ignored. Binary and newly added files yield nothing.
        """
        output = self._run(
            [
                "diff",
                "-U0",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-all-space",
                "--find-renames",
                self._object_id(parent_sha),
                self._object_id(commit_sha),
                "--",
            ],
            max_output_bytes=max_output_bytes,
        )
        removed: dict[str, list[tuple[int, str]]] = {}
        current: str | None = None
        in_header = False
        old_line = 0
        for raw in output.split(b"\n"):
            if raw.startswith(b"diff --git "):
                current, in_header, old_line = None, True, 0
                continue
            if in_header and raw.startswith(b"--- "):
                # Quoted paths (unusual characters) are skipped rather than unescaped.
                name = raw[4:].decode("utf-8", errors="surrogateescape")
                current = name[2:] if name.startswith("a/") else None
                if current is not None and (len(current) > 1_000 or "\t" in current):
                    current = None
                continue
            if raw.startswith(b"@@ "):
                in_header = False
                match = _HUNK_HEADER.match(raw.decode("ascii", errors="replace"))
                old_line = int(match.group(1)) if match else 0
                continue
            if in_header or current is None or old_line <= 0:
                continue
            if raw.startswith(b"-"):
                content = raw[1:].decode("utf-8", errors="replace")
                removed.setdefault(current, []).append((old_line, content))
                old_line += 1
        return removed

    def blame_lines(
        self, commit_sha: str, path: str, ranges: Sequence[tuple[int, int]]
    ) -> list[tuple[int, str, bool]]:
        """Attribute lines of ``path`` at ``commit_sha`` to the commits that last changed them.

        Returns (line number, commit SHA, is_boundary) per line. ``is_boundary`` marks a commit
        at the edge of the fetched (shallow) history, whose attribution is weak.
        """
        if not ranges or len(path) > 1_000 or path.startswith(("/", "-")) or "\0" in path:
            return []
        arguments = ["blame", "--porcelain", "-w"]
        for start, end in ranges:
            if start < 1 or end < start:
                raise ValueError("Blame ranges must be positive and ordered")
            arguments += ["-L", f"{start},{end}"]
        output = self._run(
            [*arguments, self._object_id(commit_sha), "--", path],
            max_output_bytes=max(200_000, sum(end - start + 1 for start, end in ranges) * 4_000),
        )
        attributed: list[tuple[int, str, bool]] = []
        boundaries: set[str] = set()
        pending: tuple[int, str] | None = None
        for raw in output.split(b"\n"):
            if raw.startswith(b"\t"):
                if pending is not None:
                    attributed.append((pending[0], pending[1], pending[1] in boundaries))
                pending = None
                continue
            line = raw.decode("utf-8", errors="replace")
            header = _BLAME_HEADER.match(line)
            if header:
                pending = (int(header.group(3)), header.group(1))
            elif line == "boundary" and pending is not None:
                boundaries.add(pending[1])
        return [
            (number, sha, sha in boundaries or boundary) for number, sha, boundary in attributed
        ]

    def read_blobs(
        self,
        entries: Sequence[RepositoryManifestFile],
        *,
        max_file_bytes: int = 400_000,
        max_total_bytes: int = 20_000_000,
    ) -> tuple[RepositorySourceFile, ...]:
        """Read pinned blobs by manifest entry, skipping oversized files and stopping at the
        total byte budget. Symlinks are never followed."""
        if max_file_bytes < 1 or max_total_bytes < 1:
            raise ValueError("Blob limits must be positive")
        files: list[RepositorySourceFile] = []
        total = 0
        for entry in sorted(entries, key=lambda item: item.path):
            if entry.mode == "120000" or entry.size > max_file_bytes:
                continue
            if not OBJECT_ID_PATTERN.fullmatch(entry.blob_sha):
                raise GitOperationError("Manifest entry has an invalid blob object ID.")
            if total + entry.size > max_total_bytes:
                break
            content = self._run(
                ["cat-file", "blob", entry.blob_sha], max_output_bytes=max_file_bytes
            )
            if len(content) != entry.size:
                raise GitOperationError("Git blob size did not match the pinned tree manifest.")
            total += entry.size
            files.append(
                RepositorySourceFile(
                    path=entry.path,
                    blob_sha=entry.blob_sha,
                    mode=entry.mode,
                    size=entry.size,
                    content=content,
                )
            )
        return tuple(files)

    def read_source_snapshot(
        self,
        ref: str,
        *,
        max_files: int = 10_000,
        max_manifest_files: int = 100_000,
        max_file_bytes: int = 1_000_000,
        max_total_bytes: int = 100_000_000,
    ) -> RepositorySourceSnapshot:
        if max_files < 1 or max_manifest_files < 1 or max_file_bytes < 1 or max_total_bytes < 1:
            raise ValueError("Repository limits must be positive")
        commit_sha = self.resolve_ref(ref)
        tree_sha = self._run(["rev-parse", "--verify", f"{commit_sha}^{{tree}}"])
        tree_id = tree_sha.decode().strip().lower()
        if not OBJECT_ID_PATTERN.fullmatch(tree_id):
            raise GitOperationError("Git returned an invalid tree object ID.")

        manifest_limit = max(1_000_000, max_manifest_files * 512)
        manifest = self._run(
            ["ls-tree", "-rz", "--long", commit_sha], max_output_bytes=manifest_limit
        )
        candidates: list[tuple[str, str, str, int]] = []
        manifest_files: list[RepositoryManifestFile] = []
        skipped: list[str] = []
        for record in manifest.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            if not separator:
                raise GitOperationError("Git returned a malformed tree record.")
            parts = metadata.decode("ascii", errors="strict").split()
            if len(parts) != 4:
                raise GitOperationError("Git returned a malformed tree record.")
            mode, object_type, blob_sha, raw_size = parts
            path = raw_path.decode("utf-8", errors="surrogateescape")
            if len(path) > 1_000:
                raise RepositoryLimitError("Repository contains a path longer than allowed.")
            pure_path = PurePosixPath(path)
            if object_type != "blob":
                continue
            try:
                size = int(raw_size)
            except ValueError as error:
                raise GitOperationError("Git returned an invalid blob size.") from error
            if not OBJECT_ID_PATTERN.fullmatch(blob_sha):
                raise GitOperationError("Git returned an invalid blob object ID.")
            manifest_files.append(
                RepositoryManifestFile(path=path, blob_sha=blob_sha, mode=mode, size=size)
            )
            if mode == "120000" or pure_path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if size > max_file_bytes:
                skipped.append(path)
                continue
            candidates.append((path, blob_sha, mode, size))

        if len(manifest_files) > max_manifest_files:
            raise RepositoryLimitError("Repository contains more files than allowed.")
        if len(candidates) > max_files:
            raise RepositoryLimitError("Repository contains more supported files than allowed.")
        if sum(item[3] for item in candidates) > max_total_bytes:
            raise RepositoryLimitError("Supported source files exceed the total byte limit.")

        files: list[RepositorySourceFile] = []
        for path, blob_sha, mode, size in sorted(candidates):
            content = self._run(["cat-file", "blob", blob_sha], max_output_bytes=max_file_bytes)
            if len(content) != size:
                raise GitOperationError("Git blob size did not match the pinned tree manifest.")
            files.append(
                RepositorySourceFile(
                    path=path,
                    blob_sha=blob_sha,
                    mode=mode,
                    size=size,
                    content=content,
                )
            )
        return RepositorySourceSnapshot(
            commit_sha=commit_sha,
            tree_sha=tree_id,
            ref=ref,
            manifest=tuple(sorted(manifest_files, key=lambda item: item.path)),
            files=tuple(files),
            skipped_oversized_files=tuple(sorted(skipped)),
        )
