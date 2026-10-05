import json
from typing import Any

import pytest
from code_genome_api.models import AuditEvent, ChatMessage
from code_genome_api.services.gemini import (
    GeminiProviderError,
    finalize_streamed_answer,
    streaming_prompt,
)
from code_genome_intelligence import RetrievalDocument
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_conversations_voice import INTRUDER, OWNER, configured_settings, setup_repository

QUESTION = "Invoice export ka workflow kahan add hua?"


class FakeStream:
    """Mimics Gemini's `streamGenerateContent?alt=sse` response body."""

    def __init__(self, pieces: list[str]) -> None:
        chunks = [{"candidates": [{"content": {"parts": [{"text": piece}]}}]} for piece in pieces]
        self.lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks]

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __iter__(self) -> Any:
        for line in self.lines:
            yield line
            yield b"\n"


def _fake_stream(pieces: list[str], prompts: list[str]) -> Any:
    def fake_urlopen(request: Any, timeout: float) -> FakeStream:
        assert request.full_url.endswith(":streamGenerateContent?alt=sse")
        assert request.headers["X-goog-api-key"] == "test-key"
        prompts.append(json.loads(request.data)["contents"][0]["parts"][0]["text"])
        return FakeStream(pieces)

    return fake_urlopen


def _events(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def _configure(
    monkeypatch: pytest.MonkeyPatch, pieces: list[str] | None, prompts: list[str]
) -> None:
    if pieces is not None:
        monkeypatch.setattr(
            "code_genome_api.services.gemini.urlopen", _fake_stream(pieces, prompts)
        )
        monkeypatch.setattr("code_genome_api.services.grounding.get_settings", configured_settings)
        monkeypatch.setattr(
            "code_genome_api.routes.conversations.get_settings", configured_settings
        )


def _start(client: TestClient, repository_id: str) -> str:
    created = client.post(
        f"/api/v1/repositories/{repository_id}/conversations", headers=OWNER, json={}
    )
    return str(created.json()["id"])


def test_streamed_hinglish_answer_is_verified_and_persisted(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_id = setup_repository(client, session_factory, "stream-ok")
    prompts: list[str] = []
    _configure(
        monkeypatch,
        ["Invoice export ka workflow ", "ek commit mein add hua tha [1", "]."],
        prompts,
    )
    conversation_id = _start(client, repository_id)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        headers=OWNER,
        json={"content": QUESTION, "language": "hinglish"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "status" and events[0][1]["model"]
    assert names.count("delta") == 3 and names[-1] == "done"
    assert "fallback" not in names
    assert "Hinglish" in prompts[0] and QUESTION in prompts[0]

    turn = events[-1][1]
    answer = turn["assistant_message"]["answer"]
    assert (
        turn["assistant_message"]["content"]
        == "Invoice export ka workflow ek commit mein add hua tha."
    )
    assert answer["evidence_ids"] and "[" not in answer["answer"]
    assert "+gemini:" in answer["retrieval_version"]
    assert answer["scope"]["language"] == "hinglish"
    assert turn["user_message"]["content"] == QUESTION
    assert turn["conversation"]["title"] == QUESTION
    assert turn["conversation"]["message_count"] == 2

    stored = client.get(f"/api/v1/conversations/{conversation_id}", headers=OWNER).json()
    assert [message["role"] for message in stored["messages"]] == ["user", "assistant"]
    with session_factory() as db:
        audit = db.scalar(select(AuditEvent).where(AuditEvent.resource_id == answer["id"]))
    assert audit is not None and audit.action == "grounded_answer.created"


def test_stream_with_invalid_citation_falls_back_to_extractive_answer(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_id = setup_repository(client, session_factory, "stream-bad")
    _configure(monkeypatch, ["Billing was rewritten in Rust [9]."], [])
    conversation_id = _start(client, repository_id)

    events = _events(
        client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            headers=OWNER,
            json={"content": "Where is invoice export?"},
        ).text
    )
    names = [name for name, _ in events]
    assert names[-2:] == ["fallback", "done"]
    answer = events[-1][1]["assistant_message"]["answer"]
    assert "Rust" not in answer["answer"]
    assert "+gemini" not in answer["retrieval_version"]
    assert answer["evidence_ids"]


def test_stream_without_gemini_returns_extractive_answer(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_id = setup_repository(client, session_factory, "stream-offline")
    conversation_id = _start(client, repository_id)
    events = _events(
        client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            headers=OWNER,
            json={"content": "Where is invoice export?"},
        ).text
    )
    assert [name for name, _ in events] == ["done"]
    assert events[0][1]["assistant_message"]["answer"]["evidence_ids"]


def test_stream_is_private_to_the_conversation_owner(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_repository(client, session_factory, "stream-private")
    conversation_id = _start(client, repository_id)
    hidden = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        headers=INTRUDER,
        json={"content": "Where is invoice export?"},
    )
    assert hidden.status_code == 404
    with session_factory() as db:
        assert (
            db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id))
            is None
        )


def test_citation_markers_map_only_to_retrieved_evidence() -> None:
    documents = (
        RetrievalDocument("commit:" + "a" * 40, "commit", "add invoice export"),
        RetrievalDocument("module:billing", "module", "Module billing"),
    )
    result = finalize_streamed_answer(
        "Export lives in billing [2] and was added [1, 2] .", documents
    )
    assert result.answer == "Export lives in billing and was added."
    assert result.evidence_ids == ("module:billing", "commit:" + "a" * 40)
    for bad in ("No citations here.", "Out of range [3].", "[1]"):
        with pytest.raises(GeminiProviderError):
            finalize_streamed_answer(bad, documents)
    prompt = streaming_prompt("Kya hua?", documents, language="hinglish")
    assert "[1] add invoice export" in prompt and "Hinglish" in prompt
