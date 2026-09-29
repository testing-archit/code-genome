from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from code_genome_evolution import miner
from git.exc import GitCommandError


class FakeModified:
    def __init__(self, path: str) -> None:
        self.new_path = path
        self.old_path = None
        self.added_lines = 3
        self.deleted_lines = 1


class FakeCommit:
    def __init__(self, sha: str, boundary: bool) -> None:
        self.hash = sha
        self.author_date = datetime(2026, 9, 1, tzinfo=UTC)
        self._boundary = boundary

    @property
    def modified_files(self) -> list[FakeModified]:
        if self._boundary:
            raise GitCommandError(["git", "diff-tree"], 128, b"fatal: bad object")
        return [FakeModified("src/index.ts")]


def test_shallow_history_boundary_commits_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRepository:
        def __init__(self, **_: Any) -> None:
            pass

        def traverse_commits(self) -> list[FakeCommit]:
            return [FakeCommit("a" * 40, boundary=True), FakeCommit("b" * 40, boundary=False)]

    monkeypatch.setattr(miner, "PyDrillerRepository", FakeRepository)
    mined = miner.mine_commit_changes(Path("/unused"), ("a" * 40, "b" * 40))
    assert [item.sha for item in mined] == ["b" * 40]
    assert mined[0].files == ("src/index.ts",) and mined[0].churn == 4
