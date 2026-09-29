import json
import re
from io import BytesIO
from typing import Any, cast

import pytest
from code_genome_api.config import Settings, get_settings
from code_genome_api.models import AuditEvent, Membership, Workspace
from code_genome_api.services.gemini_live import EVIDENCE_TOOL, REFUSAL
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_intelligence import auth, seed_intelligence

OWNER = auth("ws_intelligence", "usr_intelligence")
TEAMMATE = auth("ws_intelligence", "usr_teammate")
INTRUDER = auth("ws_intruder", "usr_intruder")


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return BytesIO(json.dumps(self.payload).encode()).read()


def configured_settings() -> Settings:
    return get_settings().model_copy(update={"gemini_api_key": SecretStr("test-key")})


def setup_repository(client: TestClient, factory: sessionmaker[Session], key: str) -> str:
    with factory() as db:
        db.add(Workspace(id="ws_intelligence", name="Intelligence"))
        db.add(Membership(workspace_id="ws_intelligence", user_id="usr_intelligence"))
        db.add(Membership(workspace_id="ws_intelligence", user_id="usr_teammate", role="member"))
        db.add(Workspace(id="ws_intruder", name="Other"))
        db.add(Membership(workspace_id="ws_intruder", user_id="usr_intruder"))
        db.commit()
    created = client.post(
        "/api/v1/repositories",
        headers={**OWNER, "Idempotency-Key": f"repo-{key}"},
        json={"clone_url": f"https://github.com/acme/{key}", "default_branch": "main"},
    )
    repository_id = cast(str, created.json()["id"])
    seed_intelligence(factory, repository_id)
    return repository_id


def test_conversation_keeps_cited_turns_private_and_resolves_follow_ups(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_repository(client, session_factory, "conversation")
    conversation = client.post(
        f"/api/v1/repositories/{repository_id}/conversations", headers=OWNER, json={}
    )
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=OWNER,
        json={"content": "Where is invoice export?"},
    )
    assert first.status_code == 201
    assistant = first.json()["assistant_message"]
    assert assistant["answer"]["evidence_ids"]
    assert assistant["answer"]["scope"]["snapshot_sha"] == "a" * 40
    assert first.json()["conversation"]["title"] == "Where is invoice export?"

    # A follow-up with no matching terms falls back to the previous user question.
    follow_up = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=OWNER,
        json={"content": "and who touched it?"},
    )
    assert follow_up.json()["assistant_message"]["answer"]["evidence_ids"]

    stored = client.get(f"/api/v1/conversations/{conversation_id}", headers=OWNER).json()
    assert [message["role"] for message in stored["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert stored["message_count"] == 4

    assert (
        client.get(f"/api/v1/conversations/{conversation_id}", headers=TEAMMATE).status_code == 404
    )
    assert (
        client.get(f"/api/v1/conversations/{conversation_id}", headers=INTRUDER).status_code == 404
    )
    assert (
        client.get(
            f"/api/v1/repositories/{repository_id}/conversations", headers=INTRUDER
        ).status_code
        == 404
    )

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}", headers=OWNER)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation_id}", headers=OWNER).status_code == 404
    with session_factory() as db:
        actions = set(db.scalars(select(AuditEvent.action)))
    assert {"conversation.created", "grounded_answer.created", "conversation.deleted"} <= actions


def test_conversation_without_published_snapshot_fails_clearly(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        db.add(Workspace(id="ws_intelligence", name="Intelligence"))
        db.add(Membership(workspace_id="ws_intelligence", user_id="usr_intelligence"))
        db.commit()
    repository_id = client.post(
        "/api/v1/repositories",
        headers={**OWNER, "Idempotency-Key": "unpublished"},
        json={"clone_url": "https://github.com/acme/unpublished", "default_branch": "main"},
    ).json()["id"]
    conversation_id = client.post(
        f"/api/v1/repositories/{repository_id}/conversations", headers=OWNER, json={}
    ).json()["id"]
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=OWNER,
        json={"content": "Where is invoice export?"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No published snapshot exists."


def _fake_gemini(prompts: list[str], rewrite_to: str) -> Any:
    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        prompt = json.loads(request.data)["contents"][0]["parts"][0]["text"]
        prompts.append(prompt)
        if "search query" in prompt:
            text = json.dumps({"query": rewrite_to})
        else:
            cited = re.findall(r"^<([^>]+)>", prompt, flags=re.MULTILINE)[:1]
            text = json.dumps(
                {"answer": "Invoice export का काम billing मॉड्यूल में है।", "cited_evidence_ids": cited}
            )
        return FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    return fake_urlopen


def test_hindi_question_is_retrieved_by_bilingual_lexicon_and_answered_in_hindi(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = setup_repository(client, session_factory, "hindi")
    prompts: list[str] = []
    monkeypatch.setattr("code_genome_api.services.gemini.urlopen", _fake_gemini(prompts, "unused"))
    monkeypatch.setattr("code_genome_api.services.grounding.get_settings", configured_settings)
    answer = client.post(
        "/api/v1/chat/answers",
        headers=OWNER,
        json={
            "repository_id": repository_id,
            "question": "बिलिंग चालान निर्यात कहाँ है?",
            "language": "hi",
        },
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["evidence_ids"]
    assert body["retrieval_version"].startswith("hybrid-bm25-lsa-rrf@1+gemini")
    assert "search_query" not in body["scope"]
    assert not any("search query" in prompt for prompt in prompts)
    assert "Hindi using Devanagari" in prompts[-1]


def test_unmatched_question_falls_back_to_model_rewritten_query(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = setup_repository(client, session_factory, "rewrite")
    prompts: list[str] = []
    monkeypatch.setattr(
        "code_genome_api.services.gemini.urlopen", _fake_gemini(prompts, "invoice export")
    )
    monkeypatch.setattr("code_genome_api.services.grounding.get_settings", configured_settings)
    body = client.post(
        "/api/v1/chat/answers",
        headers=OWNER,
        json={"repository_id": repository_id, "question": "bil banane wala hissa dikhao"},
    ).json()
    assert body["evidence_ids"]
    assert body["scope"]["search_query"] == "invoice export"
    assert any("translated English search query" in item for item in body["limitations"])


def test_voice_session_requires_configuration(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_repository(client, session_factory, "voice-off")
    response = client.post(
        "/api/v1/voice/sessions", headers=OWNER, json={"repository_id": repository_id}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "VOICE_UNAVAILABLE"


def test_voice_session_mints_constrained_token_without_exposing_key(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = setup_repository(client, session_factory, "voice-on")
    observed: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        observed["url"] = request.full_url
        observed["key"] = request.headers["X-goog-api-key"]
        observed["body"] = json.loads(request.data)
        return FakeResponse({"name": "auth_tokens/ephemeral-123"})

    monkeypatch.setattr("code_genome_api.services.gemini_live.urlopen", fake_urlopen)
    monkeypatch.setattr("code_genome_api.routes.voice.get_settings", configured_settings)

    hidden = client.post(
        "/api/v1/voice/sessions", headers=INTRUDER, json={"repository_id": repository_id}
    )
    assert hidden.status_code == 404
    assert "body" not in observed

    response = client.post(
        "/api/v1/voice/sessions",
        headers=OWNER,
        json={"repository_id": repository_id, "voice": "Puck", "language": "hinglish"},
    )
    assert response.status_code == 201
    session = response.json()
    assert session["token"] == "auth_tokens/ephemeral-123"
    assert "test-key" not in json.dumps(session)
    assert session["model"] == "gemini-3.8-live"
    assert session["websocket_url"].endswith("BidiGenerateContentConstrained")
    assert observed["key"] == "test-key"
    assert observed["url"].endswith("/v1beta/auth_tokens")

    body = observed["body"]
    assert body["uses"] == 1
    assert body["expireTime"].endswith("Z") and "fieldMask" not in body
    constraints = body["bidiGenerateContentSetup"]
    assert constraints["model"] == "models/gemini-3.8-live"
    instruction = constraints["systemInstruction"]["parts"][0]["text"]
    assert REFUSAL in instruction
    assert "Hinglish" in instruction
    assert "question argument in English" in instruction
    tool_names = [
        declaration["name"]
        for tool in constraints["tools"]
        for declaration in tool["functionDeclarations"]
    ]
    assert tool_names == [EVIDENCE_TOOL]
    assert session["setup"] == constraints
    with session_factory() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "voice_session.created"))


def test_delivery_reports_are_listed_per_repository_and_tenant(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_repository(client, session_factory, "reports")
    created = client.post(
        "/api/v1/delivery-reports",
        headers={**OWNER, "Idempotency-Key": "report-list"},
        json={
            "repository_id": repository_id,
            "text": "Added invoice export workflow.",
            "scope": {
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-30T00:00:00Z",
                "branches": ["main"],
            },
        },
    )
    assert created.status_code == 201
    listed = client.get(
        "/api/v1/delivery-reports", headers=OWNER, params={"repository_id": repository_id}
    )
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
    foreign = client.get(
        "/api/v1/delivery-reports", headers=INTRUDER, params={"repository_id": repository_id}
    )
    assert foreign.json() == []
