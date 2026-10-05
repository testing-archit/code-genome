"""Commit timestamps keep their instant regardless of the author's UTC offset."""

import os
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from code_genome_api.models import FileChange, RepositoryCommit
from code_genome_api.services.structural_analysis import _as_utc, run_structural_analysis
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_structural_analysis import FixtureCloner, seed_analysis

# 09:00 in India (+05:30) is 03:30 UTC.
AUTHORED_IST = "2026-10-06T09:00:00+05:30"
EXPECTED_UTC = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)
HEADERS = {"X-Workspace-ID": "ws_structural", "X-User-ID": "usr_structural"}


def _git(cwd: Path, *arguments: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": AUTHORED_IST, "GIT_COMMITTER_DATE": AUTHORED_IST}
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True, env=env
    )
    return result.stdout.strip()


def _ist_fixture(tmp_path: Path) -> tuple[Path, str]:
    worktree = tmp_path / "source"
    worktree.mkdir()
    _git(worktree, "init", "-b", "main")
    _git(worktree, "config", "user.name", "Fixture Author")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    (worktree / "src").mkdir()
    (worktree / "src" / "index.ts").write_text("export const value = 1;", encoding="utf-8")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "fixture authored in IST")
    sha = _git(worktree, "rev-parse", "HEAD")
    bare = tmp_path / "source.git"
    _git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return bare, sha


def _api_instant(value: str) -> datetime:
    # The API may omit the offset (SQLite); bare values are UTC by contract.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def test_commit_authored_with_offset_is_served_as_utc_instant(
    client: TestClient, session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    bare, sha = _ist_fixture(tmp_path)
    seed_analysis(session_factory)
    run_structural_analysis("run_structural", session_factory, FixtureCloner(bare))

    response = client.get("/api/v1/repositories/repo_structural/inventory", headers=HEADERS)
    assert response.status_code == 200
    commits = response.json()["commits"]
    assert [item["sha"] for item in commits] == [sha]
    assert _api_instant(commits[0]["authored_at"]) == EXPECTED_UTC

    with session_factory() as db:
        changes = list(db.scalars(select(FileChange)))
        assert changes
        assert all(_as_utc(change.authored_at) == EXPECTED_UTC for change in changes)


def test_reanalysis_corrects_rows_stored_as_author_wall_time(
    client: TestClient, session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    bare, sha = _ist_fixture(tmp_path)
    seed_analysis(session_factory)
    run_structural_analysis("run_structural", session_factory, FixtureCloner(bare))

    # Simulate rows written before normalization: the +05:30 wall time stored as if UTC.
    wall_time = datetime(2026, 10, 6, 9, 0)
    with session_factory() as db:
        for commit in db.scalars(select(RepositoryCommit)):
            commit.authored_at = wall_time
        for change in db.scalars(select(FileChange)):
            change.authored_at = wall_time
        db.commit()
    stale = client.get("/api/v1/repositories/repo_structural/inventory", headers=HEADERS)
    assert _api_instant(stale.json()["commits"][0]["authored_at"]) != EXPECTED_UTC

    # Re-analysis of the same commit reuses the snapshot but re-reads the Git objects.
    seed_analysis(session_factory, "run_reingest")
    run_structural_analysis("run_reingest", session_factory, FixtureCloner(bare))

    fixed = client.get("/api/v1/repositories/repo_structural/inventory", headers=HEADERS)
    assert fixed.status_code == 200
    assert fixed.json()["commits"][0]["sha"] == sha
    assert _api_instant(fixed.json()["commits"][0]["authored_at"]) == EXPECTED_UTC
    with session_factory() as db:
        assert all(
            _as_utc(change.authored_at) == EXPECTED_UTC for change in db.scalars(select(FileChange))
        )


def test_as_utc_handles_offsets_naive_values_and_rejects_garbage() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert _as_utc(AUTHORED_IST) == EXPECTED_UTC
    assert _as_utc(datetime(2026, 10, 6, 9, 0, tzinfo=ist)) == EXPECTED_UTC
    assert _as_utc(datetime(2026, 10, 6, 3, 30)) == EXPECTED_UTC
    try:
        _as_utc("not a timestamp")
    except ValueError:
        pass
    else:  # pragma: no cover - the assertion documents the failure path
        raise AssertionError("an invalid timestamp must not be stored")
