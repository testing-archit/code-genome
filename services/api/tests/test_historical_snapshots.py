import os
import subprocess
from pathlib import Path

import pytest
from code_genome_api.models import AnalysisRun, RepositorySnapshot
from code_genome_api.schemas import AnalysisCreate
from code_genome_api.services.structural_analysis import run_structural_analysis
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_structural_analysis import FixtureCloner, git, seed_analysis

HEADERS = {"X-Workspace-ID": "ws_structural", "X-User-ID": "usr_structural"}
BASE = "/api/v1/repositories/repo_structural"


def _commit(worktree: Path, message: str, when: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "."], cwd=worktree, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", message], cwd=worktree, check=True, env=env)
    return git(worktree, "rev-parse", "HEAD")


@pytest.fixture
def two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    worktree = tmp_path / "history"
    (worktree / "src").mkdir(parents=True)
    git(worktree, "init", "-b", "main")
    git(worktree, "config", "user.name", "Fixture")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    (worktree / "src" / "a.ts").write_text("export const a = 1;\n")
    first = _commit(worktree, "add a", "2026-08-01T10:00:00+00:00")
    (worktree / "src" / "b.ts").write_text("import { a } from './a';\nexport const b = a;\n")
    second = _commit(worktree, "add b", "2026-09-01T10:00:00+00:00")
    bare = tmp_path / "history.git"
    git(tmp_path, "clone", "--bare", "-q", str(worktree), str(bare))
    return bare, first, second


def _analyse(factory: sessionmaker[Session], bare: Path, run_id: str, as_of: str | None) -> None:
    seed_analysis(factory, run_id)
    with factory() as db:
        run = db.get(AnalysisRun, run_id)
        assert run is not None
        run.as_of = as_of
        db.commit()
    run_structural_analysis(run_id, factory, FixtureCloner(bare))


def test_past_point_analysis_adds_a_snapshot_without_replacing_the_current_one(
    client: TestClient,
    session_factory: sessionmaker[Session],
    two_commit_repo: tuple[Path, str, str],
) -> None:
    bare, first, second = two_commit_repo
    _analyse(session_factory, bare, "run_head", None)
    _analyse(session_factory, bare, "run_by_date", "2026-08-15")
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_by_date")
        assert run is not None and run.state == "SUCCEEDED", run and run.error_detail
        assert run.snapshot_sha == first
        historical = db.scalar(
            select(RepositorySnapshot).where(RepositorySnapshot.commit_sha == first)
        )
        assert historical is not None and historical.as_of == "2026-08-15"

    # Current views still read the branch head.
    inventory = client.get(f"{BASE}/inventory", headers=HEADERS).json()
    assert inventory["snapshot_sha"] == second
    graph = client.get(f"{BASE}/graph", headers=HEADERS).json()
    assert graph["scope"]["snapshot_sha"] == second
    # The past point is listed and can be compared with the head.
    listed = {
        item["commit_sha"]: item for item in client.get(f"{BASE}/snapshots", headers=HEADERS).json()
    }
    assert listed[first]["as_of"] == "2026-08-15" and listed[second]["as_of"] is None
    compared = client.get(
        f"{BASE}/compare", headers=HEADERS, params={"base": first[:12], "head": second[:12]}
    )
    assert compared.status_code == 200, compared.text


def test_a_commit_sha_outside_the_history_fails_clearly(
    client: TestClient,
    session_factory: sessionmaker[Session],
    two_commit_repo: tuple[Path, str, str],
) -> None:
    bare, _, _ = two_commit_repo
    _analyse(session_factory, bare, "run_missing", "f" * 40)
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_missing")
        assert run is not None and run.state == "FAILED"
        assert db.scalar(select(RepositorySnapshot)) is None
    _analyse(session_factory, bare, "run_too_early", "2020-01-01")
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_too_early")
        assert run is not None and run.state == "FAILED"
        assert "on or before 2020-01-01" in (run.error_detail or "")


def test_as_of_accepts_only_full_shas_and_past_dates() -> None:
    assert AnalysisCreate(as_of="2026-08-15").as_of == "2026-08-15"
    assert AnalysisCreate(as_of="A" * 40).as_of == "a" * 40
    assert AnalysisCreate(as_of="  ").as_of is None
    for bad in ("main", "abc123", "2999-01-01", "15/08/2026", "../../etc"):
        with pytest.raises(ValidationError):
            AnalysisCreate(as_of=bad)
