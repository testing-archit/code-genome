import fcntl
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

SUPPORTED_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
BRANCH_PATTERN = re.compile(r"^(?!/|.*(?:\.\.|//|@\{|\\|\s))[^~^:?*\[]+(?<![/.])$")
OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


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
        validated = validate_ref(ref)
        result = self._run(["rev-parse", "--verify", f"refs/heads/{validated}^{{commit}}"])
        object_id = result.decode().strip().lower()
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
