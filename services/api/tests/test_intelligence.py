from datetime import UTC, datetime
from typing import cast

from code_genome_api.models import (
    AnalysisRun,
    CoChangeEdge,
    FileHotspot,
    Membership,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
    Workspace,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def auth(workspace_id: str, user_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def seed_intelligence(factory: sessionmaker[Session], repository_id: str) -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    with factory() as db:
        db.add(
            AnalysisRun(
                id="run_intelligence",
                workspace_id="ws_intelligence",
                repository_id=repository_id,
                snapshot_sha="a" * 40,
                state="SUCCEEDED",
            )
        )
        db.add(
            RepositorySnapshot(
                id="snapshot_intelligence",
                workspace_id="ws_intelligence",
                repository_id=repository_id,
                commit_sha="a" * 40,
                tree_sha="b" * 40,
                run_id="run_intelligence",
                analysis_version="test@1",
                published_at=now,
            )
        )
        db.add_all(
            [
                FileHotspot(
                    id="hotspot_billing",
                    workspace_id="ws_intelligence",
                    repository_id=repository_id,
                    snapshot_id="snapshot_intelligence",
                    path="services/billing.py",
                    commit_count=10,
                    churn=500,
                    score=1,
                    evidence_shas=["a" * 40],
                ),
                FileHotspot(
                    id="hotspot_types",
                    workspace_id="ws_intelligence",
                    repository_id=repository_id,
                    snapshot_id="snapshot_intelligence",
                    path="services/types.py",
                    commit_count=1,
                    churn=10,
                    score=0.1,
                    evidence_shas=["c" * 40],
                ),
                CoChangeEdge(
                    id="cochange_1",
                    workspace_id="ws_intelligence",
                    repository_id=repository_id,
                    snapshot_id="snapshot_intelligence",
                    left_path="services/api.py",
                    right_path="services/billing.py",
                    commit_count=4,
                    confidence=0.8,
                    evidence_shas=["a" * 40],
                    analysis_version="evolution@1",
                ),
                ModuleCandidate(
                    id="module_billing",
                    workspace_id="ws_intelligence",
                    repository_id=repository_id,
                    snapshot_id="snapshot_intelligence",
                    natural_key="services/billing",
                    file_paths=["services/billing.py"],
                    confidence=0.9,
                    description="Owns invoice export and billing workflows.",
                    evidence_shas=["a" * 40],
                    analysis_version="evolution@1",
                ),
                RepositoryCommit(
                    id="commit_intelligence",
                    workspace_id="ws_intelligence",
                    repository_id=repository_id,
                    sha="a" * 40,
                    parent_shas=[],
                    author_name="Developer",
                    author_email="dev@example.com",
                    authored_at=now,
                    message="add invoice export workflow",
                ),
            ]
        )
        db.commit()


def test_risk_impact_and_grounded_answers_are_cited_and_tenant_scoped(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        db.add(Workspace(id="ws_intelligence", name="Intelligence"))
        db.add(
            Membership(workspace_id="ws_intelligence", user_id="usr_intelligence", role="owner")
        )
        db.add(Workspace(id="ws_intruder", name="Other"))
        db.add(Membership(workspace_id="ws_intruder", user_id="usr_intruder", role="owner"))
        db.commit()
    created = client.post(
        "/api/v1/repositories",
        headers={
            **auth("ws_intelligence", "usr_intelligence"),
            "Idempotency-Key": "intelligence-repo",
        },
        json={"clone_url": "https://github.com/acme/intelligence", "default_branch": "main"},
    )
    repository_id = cast(str, created.json()["id"])
    seed_intelligence(session_factory, repository_id)

    risk = client.get(
        f"/api/v1/repositories/{repository_id}/risk",
        headers=auth("ws_intelligence", "usr_intelligence"),
    )
    assert risk.status_code == 200
    assert risk.json()["scores"][0]["path"] == "services/billing.py"
    assert risk.json()["scores"][0]["features"]["relative_churn"] == 1
    assert risk.json()["scores"][0]["evidence_ids"]

    impact = client.get(
        f"/api/v1/repositories/{repository_id}/impact",
        params={"path": "services/api.py"},
        headers=auth("ws_intelligence", "usr_intelligence"),
    )
    assert impact.json()["impacted"][0]["path"] == "services/billing.py"
    assert impact.json()["impacted"][0]["evidence_ids"] == [f"commit:{'a' * 40}"]

    answer = client.post(
        "/api/v1/chat/answers",
        headers=auth("ws_intelligence", "usr_intelligence"),
        json={"repository_id": repository_id, "question": "Where is invoice export?"},
    )
    assert answer.status_code == 200
    assert answer.json()["evidence_ids"]
    assert answer.json()["scope"]["snapshot_sha"] == "a" * 40
    feedback = client.post(
        f"/api/v1/chat/answers/{answer.json()['id']}/feedback",
        headers=auth("ws_intelligence", "usr_intelligence"),
        json={"rating": 1},
    )
    assert feedback.json()["recorded"] is True

    hidden = client.get(
        f"/api/v1/repositories/{repository_id}/risk",
        headers=auth("ws_intruder", "usr_intruder"),
    )
    assert hidden.status_code == 404


def test_grounded_answer_refuses_unsupported_question(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        db.add(Workspace(id="ws_intelligence", name="Intelligence"))
        db.add(
            Membership(workspace_id="ws_intelligence", user_id="usr_intelligence", role="owner")
        )
        db.commit()
    repository = client.post(
        "/api/v1/repositories",
        headers={
            **auth("ws_intelligence", "usr_intelligence"),
            "Idempotency-Key": "intelligence-refusal",
        },
        json={"clone_url": "https://github.com/acme/refusal", "default_branch": "main"},
    ).json()
    seed_intelligence(session_factory, cast(str, repository["id"]))
    answer = client.post(
        "/api/v1/chat/answers",
        headers=auth("ws_intelligence", "usr_intelligence"),
        json={
            "repository_id": repository["id"],
            "question": "How is quantum scheduling configured?",
        },
    )
    assert answer.json()["evidence_ids"] == []
    assert answer.json()["answer"].startswith("Insufficient repository evidence")
