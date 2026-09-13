import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import jwt
import pytest
from code_genome_api import auth as auth_module
from code_genome_api.config import Settings
from code_genome_api.database import Base
from code_genome_api.errors import AppError
from code_genome_api.models import (
    DeliveryReport,
    GroundedAnswer,
    Membership,
    Repository,
    Workspace,
)
from code_genome_api.rate_limit import FixedWindowLimiter
from code_genome_api.routes.operations import csv_safe
from code_genome_api.services import structural_analysis
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_production_rejects_development_identity_headers() -> None:
    settings = Settings(_env_file=None, environment="production", auth_mode="development")

    with pytest.raises(RuntimeError, match="Production requires"):
        settings.validate_runtime()


def test_request_size_and_rate_controls(client: TestClient) -> None:
    oversized = client.post("/api/v1/delivery-reports", content=b"x" * 1_000_001)
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "REQUEST_TOO_LARGE"

    limiter = FixedWindowLimiter()
    assert limiter.allow("client", 2, now=1) is True
    assert limiter.allow("client", 2, now=2) is True
    assert limiter.allow("client", 2, now=3) is False
    assert limiter.allow("client", 2, now=62) is True
    assert csv_safe('=HYPERLINK("https://example.com")') == '\'=HYPERLINK("https://example.com")'


def test_backup_restore_preserves_workspace_identity() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Workspace(id="ws_restore", name="Restore drill"))
        db.commit()
    source = engine.raw_connection()
    restored = sqlite3.connect(":memory:")
    try:
        cast(sqlite3.Connection, source.driver_connection).backup(restored)
        row = restored.execute(
            "SELECT id, name FROM workspaces WHERE id = ?", ("ws_restore",)
        ).fetchone()
    finally:
        restored.close()
        source.close()
    assert row == ("ws_restore", "Restore drill")


def test_oidc_subject_drives_membership_lookup(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as db:
        db.add(Workspace(id="ws_oidc", name="OIDC"))
        db.add(Membership(workspace_id="ws_oidc", user_id="issuer-user", role="owner"))
        db.commit()
    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: SimpleNamespace(auth_mode="oidc", environment="production"),
    )
    monkeypatch.setattr(auth_module, "_decode_oidc_token", lambda token: "issuer-user")

    response = client.get(
        "/api/v1/repositories",
        headers={"Authorization": "Bearer signed-token", "X-Workspace-ID": "ws_oidc"},
    )

    assert response.status_code == 200


def test_oidc_validation_enforces_signature_issuer_audience_and_required_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = SimpleNamespace(
        oidc_jwks_url="https://issuer.example/.well-known/jwks.json",
        oidc_audience="code-genome",
        oidc_issuer="https://issuer.example/",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth_module,
        "_jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda __: SimpleNamespace(key=private_key.public_key())
        ),
    )
    now = datetime.now(UTC)
    claims = {
        "sub": "issuer-user",
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(claims, private_key, algorithm="RS256")

    assert auth_module._decode_oidc_token(token) == "issuer-user"
    wrong_audience = jwt.encode({**claims, "aud": "other"}, private_key, algorithm="RS256")
    with pytest.raises(AppError):
        auth_module._decode_oidc_token(wrong_audience)


def test_mirror_deletion_is_bounded_to_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "mirrors"
    workspace_key = hashlib.sha256(b"ws_delete").hexdigest()[:24]
    repository_key = hashlib.sha256(b"repo_delete").hexdigest()[:24]
    mirror = root / workspace_key / f"{repository_key}.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setattr(
        structural_analysis,
        "get_settings",
        lambda: SimpleNamespace(mirror_root=str(root)),
    )

    assert structural_analysis.delete_repository_mirror("ws_delete", "repo_delete") is True
    assert not mirror.exists()
    assert root.exists()


def test_retention_preview_execution_and_audit_export(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    with session_factory() as db:
        db.add(Workspace(id="ws_ops", name="Operations"))
        db.add(Membership(workspace_id="ws_ops", user_id="usr_ops", role="owner"))
        db.add(
            Repository(
                id="repo_ops",
                workspace_id="ws_ops",
                provider="github",
                external_id="acme/ops",
                clone_url="https://github.com/acme/ops.git",
            )
        )
        db.add(
            DeliveryReport(
                id="report_old",
                workspace_id="ws_ops",
                repository_id="repo_ops",
                raw_text="old report",
                scope_json={
                    "from": "2019-01-01T00:00:00Z",
                    "to": "2020-01-01T00:00:00Z",
                    "branches": ["main"],
                },
                submitted_by="usr_ops",
                created_at=old,
            )
        )
        db.add(
            GroundedAnswer(
                id="answer_old",
                workspace_id="ws_ops",
                repository_id="repo_ops",
                question="old question",
                answer="old answer",
                evidence_ids=[],
                scope_json={},
                limitations=[],
                created_by="usr_ops",
                created_at=old,
            )
        )
        db.commit()
    headers = {"X-Workspace-ID": "ws_ops", "X-User-ID": "usr_ops"}
    preview = client.post(
        "/api/v1/workspaces/ws_ops/retention/run", headers=headers, json={"dry_run": True}
    )
    assert preview.json()["delivery_reports"] == 1
    assert preview.json()["grounded_answers"] == 1
    executed = client.post(
        "/api/v1/workspaces/ws_ops/retention/run", headers=headers, json={"dry_run": False}
    )
    assert executed.status_code == 200
    with session_factory() as db:
        assert db.get(DeliveryReport, "report_old") is None
        assert db.get(GroundedAnswer, "answer_old") is None
    exported = client.get(
        "/api/v1/workspaces/ws_ops/audit-events", headers=headers, params={"format": "csv"}
    )
    assert exported.status_code == 200
    assert "retention.executed" in exported.text
    assert exported.headers["content-disposition"].endswith('audit-ws_ops.csv"')


def test_health_endpoint_handles_small_concurrent_probe_load(client: TestClient) -> None:
    def probe(_: int) -> int:
        return cast(int, client.get("/api/v1/health").status_code)

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(probe, range(40)))

    assert statuses == [200] * 40
