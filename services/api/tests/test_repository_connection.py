import base64

from code_genome_api.config import get_settings
from code_genome_api.models import AuditEvent, Membership, RepositoryConnection, Workspace
from code_genome_api.services.credentials import CredentialCipher, EncryptedCredential
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def auth(workspace_id: str, user_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def seed_members(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        db.add(Workspace(id="ws_private", name="Private workspace"))
        db.add(Membership(workspace_id="ws_private", user_id="usr_owner", role="owner"))
        db.add(Membership(workspace_id="ws_private", user_id="usr_viewer", role="viewer"))
        db.add(Workspace(id="ws_other_private", name="Other workspace"))
        db.add(Membership(workspace_id="ws_other_private", user_id="usr_other", role="owner"))
        db.commit()


def create_repository(client: TestClient) -> str:
    response = client.post(
        "/api/v1/repositories",
        headers={**auth("ws_private", "usr_owner"), "Idempotency-Key": "private-repo-001"},
        json={"clone_url": "https://github.com/acme/private", "default_branch": "main"},
    )
    assert response.status_code == 201
    repository_id = response.json()["id"]
    assert isinstance(repository_id, str)
    return repository_id


def test_owner_can_encrypt_rotate_and_revoke_repository_credential(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_members(session_factory)
    repository_id = create_repository(client)
    token = "github_pat_private_fixture_value"
    connected = client.put(
        f"/api/v1/repositories/{repository_id}/connection",
        headers=auth("ws_private", "usr_owner"),
        json={
            "token": token,
            "token_kind": "fine_grained_pat",
            "scopes": ["contents:read"],
        },
    )
    assert connected.status_code == 200
    assert connected.json()["connected"] is True
    assert token not in connected.text

    with session_factory() as db:
        connection = db.scalar(select(RepositoryConnection))
        assert connection is not None
        assert token not in connection.credential_ciphertext
        configured_key = get_settings().credential_encryption_key
        assert configured_key is not None
        decrypted = CredentialCipher(configured_key.get_secret_value()).decrypt(
            EncryptedCredential(connection.credential_ciphertext, connection.credential_nonce),
            "ws_private",
            repository_id,
        )
        assert decrypted == token
        assert [item.action for item in db.scalars(select(AuditEvent))] == [
            "repository.connection.upserted"
        ]

    revoked = client.delete(
        f"/api/v1/repositories/{repository_id}/connection",
        headers=auth("ws_private", "usr_owner"),
    )
    assert revoked.status_code == 204
    metadata = client.get(
        f"/api/v1/repositories/{repository_id}/connection",
        headers=auth("ws_private", "usr_owner"),
    ).json()
    assert metadata["connected"] is False
    assert metadata["token_kind"] is None
    with session_factory() as db:
        connection = db.scalar(select(RepositoryConnection))
        assert connection is not None
        assert connection.credential_ciphertext == ""
        assert connection.credential_nonce == ""
        actions = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))
        assert [item.action for item in actions] == [
            "repository.connection.upserted",
            "repository.connection.revoked",
        ]


def test_connection_mutations_enforce_role_and_tenant_scope(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    seed_members(session_factory)
    repository_id = create_repository(client)
    payload = {"token": "github_pat_denied_fixture", "scopes": ["contents:read"]}

    viewer = client.put(
        f"/api/v1/repositories/{repository_id}/connection",
        headers=auth("ws_private", "usr_viewer"),
        json=payload,
    )
    assert viewer.status_code == 403
    cross_tenant = client.put(
        f"/api/v1/repositories/{repository_id}/connection",
        headers=auth("ws_other_private", "usr_other"),
        json=payload,
    )
    assert cross_tenant.status_code == 404


def test_credential_cipher_rejects_wrong_repository_context() -> None:
    key = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode()
    cipher = CredentialCipher(key)
    envelope = cipher.encrypt("github_pat_context_fixture", "ws_one", "repo_one")

    try:
        cipher.decrypt(envelope, "ws_one", "repo_two")
    except RuntimeError as error:
        assert "could not be decrypted" in str(error)
    else:
        raise AssertionError("Context-bound credential decrypted for another repository")
