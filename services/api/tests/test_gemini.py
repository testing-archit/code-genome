import json
from io import BytesIO
from typing import Any

import pytest
from code_genome_api.services.gemini import GeminiProviderError, generate_grounded_answer
from code_genome_intelligence import RetrievalDocument


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return BytesIO(json.dumps(self.payload).encode()).read()


def provider_payload(answer: str, citations: list[str]) -> dict[str, Any]:
    content = json.dumps({"answer": answer, "cited_evidence_ids": citations})
    return {"candidates": [{"content": {"parts": [{"text": content}]}}]}


def test_gemini_answer_is_limited_to_retrieved_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        observed["url"] = request.full_url
        observed["key"] = request.headers["X-goog-api-key"]
        observed["body"] = json.loads(request.data)
        observed["timeout"] = timeout
        return FakeResponse(provider_payload("Billing owns invoice export.", ["module:billing"]))

    monkeypatch.setattr("code_genome_api.services.gemini.urlopen", fake_urlopen)
    result = generate_grounded_answer(
        api_key="secret",
        model="gemini-2.5-flash",
        question="Where is invoice export?",
        documents=(RetrievalDocument("module:billing", "module", "Billing owns invoice export."),),
        timeout_seconds=8,
        max_output_tokens=300,
    )
    assert result.answer == "Billing owns invoice export."
    assert result.evidence_ids == ("module:billing",)
    assert observed["key"] == "secret"
    assert observed["timeout"] == 8
    assert "gemini-2.5-flash:generateContent" in observed["url"]
    prompt = observed["body"]["contents"][0]["parts"][0]["text"]
    assert "<module:billing>" in prompt


def test_gemini_rejects_citations_outside_retrieved_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "code_genome_api.services.gemini.urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            provider_payload("Unsupported claim.", ["commit:invented"])
        ),
    )
    with pytest.raises(GeminiProviderError, match="outside"):
        generate_grounded_answer(
            api_key="secret",
            model="gemini-2.5-flash",
            question="What changed?",
            documents=(RetrievalDocument("commit:real", "commit", "Fix billing."),),
            timeout_seconds=8,
            max_output_tokens=300,
        )
