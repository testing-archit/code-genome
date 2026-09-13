from pathlib import Path

from pydriller import Repository as PyDrillerRepository  # type: ignore[import-untyped]

from .types import CommitChange


def mine_commit_changes(
    git_directory: Path, commit_shas: tuple[str, ...]
) -> tuple[CommitChange, ...]:
    if not commit_shas:
        return ()
    mined: list[CommitChange] = []
    repository = PyDrillerRepository(
        path_to_repo=str(git_directory),
        only_commits=list(commit_shas),
        only_no_merge=True,
        histogram_diff=True,
    )
    for commit in repository.traverse_commits():
        files: list[str] = []
        churn = 0
        for modified in commit.modified_files:
            path = modified.new_path or modified.old_path
            if path and len(path) <= 1_000:
                files.append(path)
                churn += modified.added_lines + modified.deleted_lines
        mined.append(
            CommitChange(
                sha=commit.hash,
                authored_at=commit.author_date,
                files=tuple(sorted(set(files))),
                churn=churn,
            )
        )
    return tuple(mined)
