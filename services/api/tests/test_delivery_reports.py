from datetime import UTC, datetime
from typing import cast

from code_genome_api.models import FileChange, Membership, RepositoryCommit, Workspace
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def auth(workspace_id: str, user_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def seed_workspace(factory: sessionmaker[Session], workspace_id: str, user_id: str) -> None:
    with factory() as db:
        db.add(Workspace(id=workspace_id, name=workspace_id))
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role="owner"))
        db.commit()


def register(client: TestClient, workspace_id: str, user_id: str, suffix: str) -> str:
    response = client.post(
        "/api/v1/repositories",
        headers={**auth(workspace_id, user_id), "Idempotency-Key": f"register-{suffix}"},
        json={"clone_url": f"https://github.com/acme/{suffix}", "default_branch": "main"},
    )
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def test_delivery_report_preserves_claims_and_cites_scoped_changes(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_workspace(session_factory, "ws_audit", "usr_audit")
    repository_id = register(client, "ws_audit", "usr_audit", "auditor")
    observed = datetime(2026, 9, 10, 12, tzinfo=UTC)
    sha = "a" * 40
    with session_factory() as db:
        db.add(
            RepositoryCommit(
                id="commit_1",
                workspace_id="ws_audit",
                repository_id=repository_id,
                sha=sha,
                parent_shas=[],
                author_name="Developer",
                author_email="dev@example.com",
                authored_at=observed,
                message="add invoice export endpoint",
            )
        )
        db.add(
            FileChange(
                id="change_1",
                workspace_id="ws_audit",
                repository_id=repository_id,
                snapshot_id="snapshot_1",
                commit_sha=sha,
                path="services/billing/invoice_export.py",
                authored_at=observed,
                churn=80,
            )
        )
        db.commit()

    text = "Added the invoice export endpoint. Deployed it to production."
    payload = {
        "repository_id": repository_id,
        "text": text,
        "scope": {
            "from": "2026-09-01T00:00:00Z",
            "to": "2026-09-13T23:59:59Z",
            "branches": ["main"],
        },
    }
    headers = {**auth("ws_audit", "usr_audit"), "Idempotency-Key": "report-001"}
    created = client.post("/api/v1/delivery-reports", headers=headers, json=payload)
    assert created.status_code == 201
    report = created.json()
    assert [claim["original_text"] for claim in report["claims"]] == [
        "Added the invoice export endpoint.",
        "Deployed it to production.",
    ]
    first = report["claims"][0]
    assert text[first["start_offset"] : first["end_offset"]] == first["original_text"]

    repeated = client.post("/api/v1/delivery-reports", headers=headers, json=payload)
    assert repeated.json()["id"] == report["id"]
    assessed = client.post(
        f"/api/v1/delivery-reports/{report['id']}/assessments",
        headers=auth("ws_audit", "usr_audit"),
    )
    assert assessed.status_code == 200
    claims = assessed.json()["claims"]
    assert claims[0]["assessment"]["evidence_ids"] == [
        f"change:{sha}:services/billing/invoice_export.py"
    ]
    assert claims[1]["assessment"]["status"] == "EXTERNAL_EVIDENCE_REQUIRED"
    assert claims[1]["assessment"]["limitations"]

    downloaded = client.get(
        f"/api/v1/delivery-reports/{report['id']}/download",
        headers=auth("ws_audit", "usr_audit"),
    )
    assert downloaded.status_code == 200
    assert "# Delivery audit" in downloaded.text
    assert "EXTERNAL_EVIDENCE_REQUIRED" in downloaded.text


def test_delivery_report_is_hidden_across_workspaces(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_workspace(session_factory, "ws_owner", "usr_owner")
    seed_workspace(session_factory, "ws_other", "usr_other")
    repository_id = register(client, "ws_owner", "usr_owner", "private-audit")
    created = client.post(
        "/api/v1/delivery-reports",
        headers={**auth("ws_owner", "usr_owner"), "Idempotency-Key": "report-tenant"},
        json={
            "repository_id": repository_id,
            "text": "Changed private billing logic.",
            "scope": {
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-13T23:59:59Z",
                "branches": ["main"],
            },
        },
    )
    response = client.get(
        f"/api/v1/delivery-reports/{created.json()['id']}",
        headers=auth("ws_other", "usr_other"),
    )
    assert response.status_code == 404
