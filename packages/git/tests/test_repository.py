import subprocess
from pathlib import Path

import pytest
from code_genome_git import (
    GitOperationError,
    GitRepository,
    RepositoryLimitError,
    normalize_github_url,
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
