import subprocess
from pathlib import Path
from typing import Any

import pytest
from code_genome_git import (
    GitCredential,
    GitOperationError,
    GitRepository,
    RepositoryLimitError,
    normalize_github_url,
    sync_github_repository,
    validate_ref,
)


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def bare_repository(tmp_path: Path) -> tuple[GitRepository, str]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    git(worktree, "init", "-b", "main")
    git(worktree, "config", "user.name", "Fixture Author")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    (worktree / "src").mkdir()
    (worktree / "src" / "index.ts").write_text(
        'import { value } from "./value";\nexport const result = value;', encoding="utf-8"
    )
    (worktree / "src" / "value.ts").write_text("export const value = 42;", encoding="utf-8")
    (worktree / "README.md").write_text("fixture", encoding="utf-8")
    (worktree / "src" / "oversized.js").write_text("x" * 200, encoding="utf-8")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "fixture")
    commit_sha = git(worktree, "rev-parse", "HEAD")

    bare = tmp_path / "repository.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return GitRepository(bare), commit_sha


def test_reads_only_supported_blobs_from_a_pinned_snapshot(
    bare_repository: tuple[GitRepository, str],
) -> None:
    repository, expected_commit = bare_repository
    snapshot = repository.read_source_snapshot("main", max_file_bytes=100)

    assert snapshot.commit_sha == expected_commit
    assert len(snapshot.tree_sha) == 40
    assert [item.path for item in snapshot.files] == ["src/index.ts", "src/value.ts"]
    assert snapshot.files[0].content.startswith(b"import")
    assert snapshot.skipped_oversized_files == ("src/oversized.js",)
    assert [item.path for item in snapshot.manifest] == [
        "README.md",
        "src/index.ts",
        "src/oversized.js",
        "src/value.ts",
    ]

    refs = repository.list_branch_refs()
    commits = repository.read_commit_history("main")
    assert refs[0].name == "main"
    assert refs[0].head_sha == expected_commit
    assert commits[0].sha == expected_commit
    assert commits[0].message == "fixture"


def test_enforces_file_and_total_size_limits(bare_repository: tuple[GitRepository, str]) -> None:
    repository, _ = bare_repository
    with pytest.raises(RepositoryLimitError, match="more supported files"):
        repository.read_source_snapshot("main", max_files=1)
    with pytest.raises(RepositoryLimitError, match="total byte limit"):
        repository.read_source_snapshot("main", max_file_bytes=1_000, max_total_bytes=100)


def test_rejects_untrusted_urls_and_refs(bare_repository: tuple[GitRepository, str]) -> None:
    assert normalize_github_url("https://github.com/acme/widget") == (
        "https://github.com/acme/widget.git"
    )
    for unsafe_url in (
        "file:///tmp/repo",
        "https://token@github.com/acme/widget",
        "https://github.com/acme/widget?token=secret",
        "https://example.com/acme/widget",
    ):
        with pytest.raises(ValueError):
            normalize_github_url(unsafe_url)

    for unsafe_ref in ("../main", "main lock", "feature..branch", "refs/@{danger"):
        with pytest.raises(ValueError):
            validate_ref(unsafe_ref)

    repository, _ = bare_repository
    with pytest.raises(GitOperationError):
        repository.resolve_ref("missing")


def test_incremental_fetch_uses_a_credential_without_putting_it_in_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from code_genome_git import repository as repository_module

    mirror = tmp_path / "mirror.git"
    mirror.mkdir()
    calls: list[tuple[list[str], GitCredential | None]] = []

    def fake_run_git(
        arguments: list[str],
        *,
        timeout_seconds: int,
        max_output_bytes: int | None = None,
        credential: GitCredential | None = None,
    ) -> bytes:
        del timeout_seconds, max_output_bytes
        calls.append((arguments, credential))
        if "--is-bare-repository" in arguments:
            return b"true\n"
        if "get-url" in arguments:
            return b"https://github.com/acme/private.git\n"
        return b""

    monkeypatch.setattr(repository_module, "_run_git", fake_run_git)
    credential = GitCredential(token="github_pat_secret_fixture")
    synced = sync_github_repository(
        "https://github.com/acme/private",
        mirror,
        "main",
        credential=credential,
    )

    assert synced.git_directory == mirror
    fetch_arguments, fetch_credential = calls[-1]
    assert "fetch" in fetch_arguments
    assert fetch_credential is credential
    assert all(credential.token not in argument for argument in fetch_arguments)
    assert "secret_fixture" not in repr(credential)


def test_askpass_secret_is_ephemeral_and_git_errors_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_genome_git import repository as repository_module

    observed: dict[str, Any] = {}

    def fake_subprocess_run(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        environment = kwargs["env"]
        helper = Path(environment["GIT_ASKPASS"])
        observed["arguments"] = arguments
        observed["secret"] = environment["CODE_GENOME_GIT_PASSWORD"]
        observed["helper"] = helper
        observed["helper_body"] = helper.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            arguments, 1, stdout=b"", stderr=b"fatal: github_pat_redact_fixture rejected"
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    credential = GitCredential(token="github_pat_redact_fixture")
    with pytest.raises(GitOperationError, match=r"\[REDACTED\]"):
        repository_module._run_git(["fetch", "origin"], timeout_seconds=1, credential=credential)

    assert credential.token not in observed["arguments"]
    assert observed["secret"] == credential.token
    assert credential.token not in observed["helper_body"]
    assert not observed["helper"].exists()
