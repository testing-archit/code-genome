from code_genome_api.models import AnalysisRun, Membership, Workspace
from code_genome_api.services.analysis import run_fake_analysis
from sqlalchemy.orm import Session, sessionmaker


def auth(workspace_id: str, user_id: str = "usr_one") -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def seed_workspace(factory: sessionmaker[Session], workspace_id: str, user_id: str) -> None:
    with factory() as db:
        db.add(Workspace(id=workspace_id, name=workspace_id))
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role="owner"))
        db.commit()


def test_repository_and_analysis_lifecycle(client, session_factory: sessionmaker[Session]) -> None:
    seed_workspace(session_factory, "ws_one", "usr_one")
    headers = {**auth("ws_one"), "Idempotency-Key": "register-001"}
    response = client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"clone_url": "https://github.com/acme/widget", "default_branch": "main"},
    )
    assert response.status_code == 201
    repository = response.json()
    assert repository["external_id"] == "acme/widget"
    assert repository["clone_url"] == "https://github.com/acme/widget.git"

    repeated = client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"clone_url": "https://github.com/acme/widget", "default_branch": "main"},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == repository["id"]

    queued = client.post(
        f"/api/v1/repositories/{repository['id']}/analyses",
        headers={**auth("ws_one"), "Idempotency-Key": "analysis-001"},
        json={"refs": ["main"]},
    )
    assert queued.status_code == 202
    assert queued.json()["state"] == "QUEUED"

    run_fake_analysis(queued.json()["id"], session_factory)
    completed = client.get(f"/api/v1/analyses/{queued.json()['id']}", headers=auth("ws_one"))
    assert completed.status_code == 200
    assert completed.json()["state"] == "SUCCEEDED"
    assert completed.json()["snapshot_sha"] is None
    assert "No repository evidence was analyzed" in completed.json()["diagnostics"][0]


def test_resources_are_hidden_across_workspaces(
    client, session_factory: sessionmaker[Session]
) -> None:
    seed_workspace(session_factory, "ws_one", "usr_one")
    seed_workspace(session_factory, "ws_two", "usr_two")
    created = client.post(
        "/api/v1/repositories",
        headers={**auth("ws_one"), "Idempotency-Key": "register-002"},
        json={"clone_url": "https://github.com/acme/private", "default_branch": "main"},
    )
    repository_id = created.json()["id"]

    response = client.post(
        f"/api/v1/repositories/{repository_id}/analyses",
        headers={**auth("ws_two", "usr_two"), "Idempotency-Key": "analysis-002"},
        json={"refs": ["main"]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_untrusted_repository_urls_and_refs_are_rejected(
    client, session_factory: sessionmaker[Session]
) -> None:
    seed_workspace(session_factory, "ws_one", "usr_one")
    response = client.post(
        "/api/v1/repositories",
        headers={**auth("ws_one"), "Idempotency-Key": "register-003"},
        json={"clone_url": "https://token@github.com/acme/widget", "default_branch": "../main"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fake_job_records_a_safe_failure(client, session_factory: sessionmaker[Session]) -> None:
    seed_workspace(session_factory, "ws_one", "usr_one")
    repository = client.post(
        "/api/v1/repositories",
        headers={**auth("ws_one"), "Idempotency-Key": "register-004"},
        json={"clone_url": "https://github.com/acme/failure", "default_branch": "main"},
    ).json()
    queued = client.post(
        f"/api/v1/repositories/{repository['id']}/analyses",
        headers={**auth("ws_one"), "Idempotency-Key": "analysis-004"},
        json={"refs": ["main"], "simulate_failure": True},
    ).json()

    run_fake_analysis(queued["id"], session_factory)
    with session_factory() as db:
        run = db.get(AnalysisRun, queued["id"])
        assert run is not None
        assert run.state == "FAILED"
        assert run.error_code == "SIMULATED_FAILURE"
