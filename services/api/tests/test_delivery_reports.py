from datetime import UTC, datetime
from typing import cast

import pytest
from code_genome_api.models import (
    AnalysisRun,
    FileChange,
    Membership,
    RepositoryCommit,
    RepositorySnapshot,
    Workspace,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def auth(workspace_id: str, user_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def seed_snapshot(db: Session, workspace_id: str, repository_id: str, snapshot_id: str) -> None:
    """File changes reference a snapshot; Postgres enforces that foreign key."""
    run_id = f"run_{snapshot_id}"
    db.add(
        AnalysisRun(
            id=run_id,
            workspace_id=workspace_id,
            repository_id=repository_id,
            requested_refs=["main"],
            state="SUCCEEDED",
            version="structural-genome@test",
        )
    )
    db.add(
        RepositorySnapshot(
            id=snapshot_id,
            workspace_id=workspace_id,
            repository_id=repository_id,
            commit_sha=snapshot_id.ljust(40, "0"),
            tree_sha="t" * 40,
            run_id=run_id,
            analysis_version="structural-genome@test",
        )
    )


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
        seed_snapshot(db, "ws_audit", repository_id, "snapshot_1")
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


def test_assessment_counts_each_change_once_across_snapshots(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Every snapshot stores its own FileChange rows, so overlapping history repeats a
    (commit, path) pair; the audit must count and cite it once. A cited path containing
    a colon must still count as cited."""
    seed_workspace(session_factory, "ws_dupes", "usr_dupes")
    repository_id = register(client, "ws_dupes", "usr_dupes", "dupes")
    observed = datetime(2026, 9, 10, 12, tzinfo=UTC)
    sha, other = "b" * 40, "c" * 40
    commits = {
        sha: ("add invoice export endpoint", "services/billing/invoice:export.py"),
        other: ("bump retry settings", "config/settings.yaml"),
    }
    with session_factory() as db:
        for snapshot in ("snapshot_old", "snapshot_new"):
            seed_snapshot(db, "ws_dupes", repository_id, snapshot)
        for commit_sha, (message, path) in commits.items():
            db.add(
                RepositoryCommit(
                    id=f"commit_{commit_sha[0]}",
                    workspace_id="ws_dupes",
                    repository_id=repository_id,
                    sha=commit_sha,
                    parent_shas=[],
                    author_name="Developer",
                    author_email="dev@example.com",
                    authored_at=observed,
                    message=message,
                )
            )
            for snapshot in ("snapshot_old", "snapshot_new"):
                db.add(
                    FileChange(
                        id=f"change_{snapshot}_{commit_sha[0]}",
                        workspace_id="ws_dupes",
                        repository_id=repository_id,
                        snapshot_id=snapshot,
                        commit_sha=commit_sha,
                        path=path,
                        authored_at=observed,
                        churn=10,
                    )
                )
        db.commit()

    created = client.post(
        "/api/v1/delivery-reports",
        headers={**auth("ws_dupes", "usr_dupes"), "Idempotency-Key": "report-dupes"},
        json={
            "repository_id": repository_id,
            "text": "Added the invoice export endpoint.",
            "scope": {
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-13T23:59:59Z",
                "branches": ["main"],
            },
        },
    )
    assert created.status_code == 201
    assessed = client.post(
        f"/api/v1/delivery-reports/{created.json()['id']}/assessments",
        headers=auth("ws_dupes", "usr_dupes"),
    )
    assert assessed.status_code == 200
    report = assessed.json()
    evidence = report["claims"][0]["assessment"]["evidence_ids"]
    assert evidence == [f"change:{sha}:services/billing/invoice:export.py"]
    [unreported] = report["unreported_changes"]
    assert unreported["path"] == "config/settings.yaml"
    assert unreported["evidence_ids"] == [f"change:{other}:config/settings.yaml"]
    assert "Observed in 1 scoped commit(s)" in unreported["explanation"]


def test_ci_and_deployment_claims_use_github_evidence(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_genome_api.models import AuditEvent, ProviderSignal
    from code_genome_api.services import provider_evidence

    seed_workspace(session_factory, "ws_ci", "usr_ci")
    repository_id = register(client, "ws_ci", "usr_ci", "ci-repo")
    sha = "d" * 40
    observed = datetime(2026, 9, 10, 12, tzinfo=UTC)
    with session_factory() as db:
        seed_snapshot(db, "ws_ci", repository_id, "snapshot_ci")
        db.add(
            RepositoryCommit(
                id="commit_ci",
                workspace_id="ws_ci",
                repository_id=repository_id,
                sha=sha,
                parent_shas=[],
                author_name="Dev",
                author_email="dev@example.com",
                authored_at=observed,
                message="add invoice export",
            )
        )
        db.add(
            FileChange(
                id="change_ci",
                workspace_id="ws_ci",
                repository_id=repository_id,
                snapshot_id="snapshot_ci",
                commit_sha=sha,
                path="src/export.ts",
                authored_at=observed,
                churn=12,
            )
        )
        db.commit()
    calls: list[str] = []

    def fake(url: str, token: str | None) -> object:
        calls.append(url)
        if url.endswith("/statuses?per_page=1"):
            return [{"state": "success", "created_at": "2026-09-10T13:00:00Z"}]
        if "/check-runs" in url:
            return {
                "check_runs": [
                    {"id": 11, "name": "test", "status": "completed", "conclusion": "success"},
                    {"id": 12, "name": "lint", "status": "in_progress", "conclusion": None},
                ]
            }
        return [{"id": 77, "sha": sha, "environment": "production"}]

    monkeypatch.setattr(provider_evidence, "github_fetch", fake)
    created = client.post(
        "/api/v1/delivery-reports",
        headers={**auth("ws_ci", "usr_ci"), "Idempotency-Key": "report-ci"},
        json={
            "repository_id": repository_id,
            "text": "Added invoice export. All tests pass. Deployed it to production.",
            "scope": {
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-13T23:59:59Z",
                "branches": ["main"],
                "include_ci": True,
                "include_deployments": True,
            },
        },
    )
    assert created.status_code == 201
    assessed = client.post(
        f"/api/v1/delivery-reports/{created.json()['id']}/assessments",
        headers=auth("ws_ci", "usr_ci"),
    )
    assert assessed.status_code == 200, assessed.text
    claims = {item["claim_type"]: item["assessment"] for item in assessed.json()["claims"]}
    assert claims["test"]["status"] == "VERIFIED"
    assert claims["deployment"]["status"] == "VERIFIED"
    assert all(url.startswith("https://api.github.com/repos/acme/ci-repo/") for url in calls)
    signal_id = claims["deployment"]["evidence_ids"][0].removeprefix("provider:")
    detail = client.get(
        f"/api/v1/delivery-reports/signals/{signal_id}", headers=auth("ws_ci", "usr_ci")
    )
    assert detail.status_code == 200
    assert detail.json()["environment"] == "production"
    assert detail.json()["outcome"] == "success"
    hidden = client.get(
        f"/api/v1/delivery-reports/signals/{signal_id}", headers=auth("ws_other", "usr_other")
    )
    assert hidden.status_code in {403, 404}
    with session_factory() as db:
        rows = list(db.scalars(select(ProviderSignal)))
        assert {row.kind for row in rows} == {"ci_run", "deployment"}
        assert all(row.raw_json and row.workspace_id == "ws_ci" for row in rows)
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "delivery_report.assessed"))


def test_provider_outage_keeps_claims_unverified_with_a_limitation(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_workspace(session_factory, "ws_down", "usr_down")
    repository_id = register(client, "ws_down", "usr_down", "down-repo")
    with session_factory() as db:
        seed_snapshot(db, "ws_down", repository_id, "snapshot_down")
        db.add(
            FileChange(
                id="change_down",
                workspace_id="ws_down",
                repository_id=repository_id,
                snapshot_id="snapshot_down",
                commit_sha="e" * 40,
                path="src/a.ts",
                authored_at=datetime(2026, 9, 10, tzinfo=UTC),
                churn=1,
            )
        )
        db.commit()
    created = client.post(
        "/api/v1/delivery-reports",
        headers={**auth("ws_down", "usr_down"), "Idempotency-Key": "report-down"},
        json={
            "repository_id": repository_id,
            "text": "Tests are passing.",
            "scope": {
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-13T23:59:59Z",
                "include_ci": True,
            },
        },
    )
    assessed = client.post(
        f"/api/v1/delivery-reports/{created.json()['id']}/assessments",
        headers=auth("ws_down", "usr_down"),
    )
    assessment = assessed.json()["claims"][0]["assessment"]
    assert assessment["status"] == "EXTERNAL_EVIDENCE_REQUIRED"
    assert any("incomplete" in item for item in assessment["limitations"])
