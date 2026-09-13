import os
import re
import subprocess
from dataclasses import dataclass
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


def _git_environment() -> dict[str, str]:
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
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key)
    return environment


def _safe_error(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return text[-1][:300] if text else "Git operation failed without diagnostic output."


def _run_git(
    arguments: list[str], *, timeout_seconds: int, max_output_bytes: int | None = None
) -> bytes:
    try:
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
            env=_git_environment(),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise GitOperationError("Git operation exceeded its configured time limit.") from error
    except OSError as error:
        raise GitOperationError("Git executable could not be started.") from error
    if completed.returncode != 0:
        raise GitOperationError(_safe_error(completed.stderr))
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
    )
    return GitRepository(destination, timeout_seconds=timeout_seconds)


class GitRepository:
    def __init__(self, git_directory: Path, *, timeout_seconds: int = 30) -> None:
        resolved = git_directory.resolve()
        if not resolved.is_dir():
            raise ValueError("Git directory does not exist")
        self.git_directory = resolved
        self.timeout_seconds = timeout_seconds

    def _run(self, arguments: list[str], *, max_output_bytes: int | None = None) -> bytes:
        return _run_git(
            ["--git-dir", str(self.git_directory), *arguments],
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    def resolve_ref(self, ref: str) -> str:
        validated = validate_ref(ref)
        result = self._run(["rev-parse", "--verify", f"refs/heads/{validated}^{{commit}}"])
        object_id = result.decode().strip().lower()
        if not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise GitOperationError("Git returned an invalid commit object ID.")
        return object_id

    def read_source_snapshot(
        self,
        ref: str,
        *,
        max_files: int = 10_000,
        max_file_bytes: int = 1_000_000,
        max_total_bytes: int = 100_000_000,
    ) -> RepositorySourceSnapshot:
        if max_files < 1 or max_file_bytes < 1 or max_total_bytes < 1:
            raise ValueError("Repository limits must be positive")
        commit_sha = self.resolve_ref(ref)
        tree_sha = self._run(["rev-parse", "--verify", f"{commit_sha}^{{tree}}"])
        tree_id = tree_sha.decode().strip().lower()
        if not OBJECT_ID_PATTERN.fullmatch(tree_id):
            raise GitOperationError("Git returned an invalid tree object ID.")

        manifest_limit = max(1_000_000, max_files * 512)
        manifest = self._run(
            ["ls-tree", "-rz", "--long", commit_sha], max_output_bytes=manifest_limit
        )
        candidates: list[tuple[str, str, str, int]] = []
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
            pure_path = PurePosixPath(path)
            if (
                object_type != "blob"
                or mode == "120000"
                or pure_path.suffix.lower() not in SUPPORTED_SUFFIXES
            ):
                continue
            try:
                size = int(raw_size)
            except ValueError as error:
                raise GitOperationError("Git returned an invalid blob size.") from error
            if size > max_file_bytes:
                skipped.append(path)
                continue
            candidates.append((path, blob_sha, mode, size))

        if len(candidates) > max_files:
            raise RepositoryLimitError("Repository contains more supported files than allowed.")
        if sum(item[3] for item in candidates) > max_total_bytes:
            raise RepositoryLimitError("Supported source files exceed the total byte limit.")

        files: list[RepositorySourceFile] = []
        for path, blob_sha, mode, size in sorted(candidates):
            if not OBJECT_ID_PATTERN.fullmatch(blob_sha):
                raise GitOperationError("Git returned an invalid blob object ID.")
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
            files=tuple(files),
            skipped_oversized_files=tuple(sorted(skipped)),
        )
