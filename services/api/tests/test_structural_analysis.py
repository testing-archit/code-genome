import subprocess
from pathlib import Path

from code_genome_api.models import (
    AnalysisRun,
    BranchRef,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    Membership,
    Repository,
    RepositoryCommit,
    RepositorySnapshot,
    Workspace,
)
from code_genome_api.services.structural_analysis import run_structural_analysis
from code_genome_git import GitCredential, GitOperationError, GitRepository
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


class FixtureCloner:
    def __init__(self, git_directory: Path) -> None:
        self.git_directory = git_directory

    def __call__(
        self,
        clone_url: str,
        destination: Path,
        branch: str,
        *,
        depth: int,
        timeout_seconds: int,
        credential: GitCredential | None,
    ) -> GitRepository:
        del clone_url, destination, branch, depth, timeout_seconds, credential
        return GitRepository(self.git_directory)


class FailingCloner:
    def __call__(
        self,
        clone_url: str,
        destination: Path,
        branch: str,
        *,
        depth: int,
        timeout_seconds: int,
        credential: GitCredential | None,
    ) -> GitRepository:
        del clone_url, destination, branch, depth, timeout_seconds, credential
        raise GitOperationError("fixture clone failure")


def create_bare_fixture(tmp_path: Path) -> tuple[Path, str]:
    worktree = tmp_path / "source"
    worktree.mkdir()
    git(worktree, "init", "-b", "main")
    git(worktree, "config", "user.name", "Fixture Author")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    (worktree / "src").mkdir()
    (worktree / "src" / "index.ts").write_text(
        'import { format } from "./format";\nexport const result = format("ok");',
        encoding="utf-8",
    )
    (worktree / "src" / "format.ts").write_text(
        "export function format(value: string) { return value; }", encoding="utf-8"
    )
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "fixture")
    commit_sha = git(worktree, "rev-parse", "HEAD")
    bare = tmp_path / "source.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return bare, commit_sha


def seed_analysis(factory: sessionmaker[Session], run_id: str = "run_structural") -> None:
    with factory() as db:
        if db.get(Workspace, "ws_structural") is None:
            db.add(Workspace(id="ws_structural", name="Structural test"))
            db.add(Membership(workspace_id="ws_structural", user_id="usr_structural", role="owner"))
            db.add(
                Repository(
                    id="repo_structural",
                    workspace_id="ws_structural",
                    provider="github",
                    external_id="fixture/structural",
                    clone_url="https://github.com/fixture/structural.git",
                    default_branch="main",
                )
            )
        db.add(
            AnalysisRun(
                id=run_id,
                workspace_id="ws_structural",
                repository_id="repo_structural",
                requested_refs=["main"],
                state="QUEUED",
                version="structural-genome@0.1.0",
            )
        )
        db.commit()


def test_publishes_and_serves_an_immutable_structural_graph(
    client: TestClient, session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    bare, expected_sha = create_bare_fixture(tmp_path)
    seed_analysis(session_factory)
    run_structural_analysis("run_structural", session_factory, FixtureCloner(bare))

    with session_factory() as db:
        run = db.get(AnalysisRun, "run_structural")
        snapshot = db.scalar(select(RepositorySnapshot))
        assert run is not None and run.state == "SUCCEEDED"
        assert run.snapshot_sha == expected_sha
        assert snapshot is not None and snapshot.published_at is not None
        assert db.scalar(select(func.count()).select_from(GraphNode)) == 3
        assert db.scalar(select(func.count()).select_from(GraphEdge)) == 3
        assert db.scalar(select(func.count()).select_from(BranchRef)) == 1
        assert db.scalar(select(func.count()).select_from(RepositoryCommit)) == 1
        assert db.scalar(select(func.count()).select_from(FileManifestEntry)) == 2
        assert all(
            workspace_id == "ws_structural"
            for workspace_id in db.scalars(select(GraphNode.workspace_id))
        )

    headers = {"X-Workspace-ID": "ws_structural", "X-User-ID": "usr_structural"}
    response = client.get("/api/v1/repositories/repo_structural/graph?limit=100", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert graph["scope"]["snapshot_sha"] == expected_sha
    assert len(graph["nodes"]) == 3
    assert all(node["evidence_ids"] for node in graph["nodes"])
    evidence_id = graph["nodes"][0]["evidence_ids"][0]
    evidence = client.get(f"/api/v1/evidence/{evidence_id}", headers=headers)
    assert evidence.status_code == 200
    assert evidence.json()["repository_sha"] == expected_sha
    inventory = client.get("/api/v1/repositories/repo_structural/inventory", headers=headers)
    assert inventory.status_code == 200
    assert inventory.json()["refs"][0]["head_sha"] == expected_sha
    assert [item["path"] for item in inventory.json()["files"]] == [
        "src/format.ts",
        "src/index.ts",
    ]
    architecture = client.get(
        "/api/v1/repositories/repo_structural/architecture", headers=headers
    )
    assert architecture.status_code == 200
    module = architecture.json()["modules"][0]
    assert module["name"] == "src"
    assert module["inferred"] is True
    assert module["description"].startswith("Inferred")
    assert module["citations"] == [f"commit:{expected_sha}"]

    seed_analysis(session_factory, "run_repeated")
    run_structural_analysis("run_repeated", session_factory, FixtureCloner(bare))
    with session_factory() as db:
        repeated = db.get(AnalysisRun, "run_repeated")
        assert repeated is not None and repeated.state == "SUCCEEDED"
        assert db.scalar(select(func.count()).select_from(RepositorySnapshot)) == 1
        assert repeated.diagnostics == [
            "Reused the existing immutable snapshot for this repository commit."
        ]


def test_clone_failure_publishes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    seed_analysis(session_factory, "run_failure")
    run_structural_analysis("run_failure", session_factory, FailingCloner())

    with session_factory() as db:
        run = db.get(AnalysisRun, "run_failure")
        assert run is not None and run.state == "FAILED"
        assert run.error_code == "GIT_OPERATION_FAILED"
        assert run.error_detail == "The pinned repository source could not be retrieved."
        assert db.scalar(select(func.count()).select_from(RepositorySnapshot)) == 0


def test_graph_is_hidden_from_another_workspace(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_analysis(session_factory)
    with session_factory() as db:
        db.add(Workspace(id="ws_other", name="Other"))
        db.add(Membership(workspace_id="ws_other", user_id="usr_other", role="owner"))
        db.commit()
    response = client.get(
        "/api/v1/repositories/repo_structural/graph",
        headers={"X-Workspace-ID": "ws_other", "X-User-ID": "usr_other"},
    )
    assert response.status_code == 404
