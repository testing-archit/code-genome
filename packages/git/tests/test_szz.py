import subprocess
from pathlib import Path

import pytest
from code_genome_git import GitRepository, is_fix_message, trace_bug_introductions


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def commit(worktree: Path, message: str, author: str = "Fixture Author") -> str:
    git(worktree, "add", ".")
    git(
        worktree,
        "-c",
        f"user.name={author}",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(worktree, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> tuple[GitRepository, dict[str, str]]:
    worktree = tmp_path / "work"
    (worktree / "src").mkdir(parents=True)
    git(worktree, "init", "-b", "main")
    source = worktree / "src" / "total.ts"
    source.write_text(
        "export function total(items: number[]) {\n"
        "  let sum = 0;\n"
        "  for (const item of items) sum += item;\n"
        "  return sum;\n"
        "}\n"
    )
    shas = {"initial": commit(worktree, "add totals")}
    source.write_text(
        "export function total(items: number[]) {\n"
        "  let sum = 0;\n"
        "  // apply discount\n"
        "  for (const item of items) sum += item * 0.9;\n"
        "  return sum;\n"
        "}\n"
    )
    shas["introducing"] = commit(worktree, "apply discount to totals", "Bug Author")
    (worktree / "README.md").write_text("docs\n")
    shas["unrelated"] = commit(worktree, "docs: readme")
    source.write_text(
        "export function total(items: number[]) {\n"
        "  let sum = 0;\n"
        "  // apply discount\n"
        "  for (const item of items) sum += item;\n"
        "  return sum * 0.9;\n"
        "}\n"
    )
    shas["fix"] = commit(worktree, "fix: discount applied per item instead of once")
    bare = tmp_path / "repo.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return GitRepository(bare), shas


def test_fix_rule_is_word_bounded_on_the_subject() -> None:
    assert is_fix_message("fix: null total")
    assert is_fix_message("Hotfix crash on login")
    assert is_fix_message("Handle Errors from API")
    assert not is_fix_message("prefix the bucket names")
    assert not is_fix_message("add debugger panel\n\nfixes nothing in body only")
    assert not is_fix_message("")


def test_traces_fix_back_to_the_bug_introducing_commit(
    history: tuple[GitRepository, dict[str, str]],
) -> None:
    repository, shas = history
    commits = repository.read_commit_history("main")
    result = trace_bug_introductions(repository, commits)

    assert result.fix_shas == (shas["fix"],)
    assert result.examined_fixes == 1
    introducing = {link.introducing_sha for link in result.links}
    # Line 4 (item * 0.9) came from the introducing commit; line 5 (return sum) predates it.
    assert shas["introducing"] in introducing
    link = next(item for item in result.links if item.introducing_sha == shas["introducing"])
    assert link.path == "src/total.ts"
    assert link.fix_sha == shas["fix"]
    assert link.lines == 1
    assert link.blamed_ranges == ((4, 4),)
    assert 0 < link.confidence < 1
    # The unrelated docs commit is never blamed, and the comment line is ignored.
    assert shas["unrelated"] not in introducing
    assert all(item.introducing_sha != shas["fix"] for item in result.links)


def test_no_fix_commits_yields_no_links(tmp_path: Path) -> None:
    worktree = tmp_path / "plain"
    worktree.mkdir()
    git(worktree, "init", "-b", "main")
    (worktree / "a.ts").write_text("export const a = 1;\n")
    commit(worktree, "initial")
    bare = tmp_path / "plain.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    repository = GitRepository(bare)
    result = trace_bug_introductions(repository, repository.read_commit_history("main"))
    assert result.links == () and result.fix_shas == ()


def test_blame_rejects_invalid_object_ids(
    history: tuple[GitRepository, dict[str, str]],
) -> None:
    repository, _ = history
    with pytest.raises(ValueError):
        repository.blame_lines("HEAD", "src/total.ts", [(1, 1)])
    with pytest.raises(ValueError):
        repository.removed_lines("main", "HEAD")
