from pathlib import Path

from code_genome_api.services.question_routing import is_follow_up
from fastapi.testclient import TestClient
from test_project_knowledge import HEADERS, analysed  # noqa: F401


def test_follow_up_detection() -> None:
    for question in (
        "tell mw again",
        "explain that again",
        "aur batao",
        "phir se samjhao",
        "why?",
        "more detail please",
    ):
        assert is_follow_up(question), question
    for question in (
        "What does this project do?",
        "If I change src/format.ts what breaks?",
        "how does the invoice export handle currency rounding",
    ):
        assert not is_follow_up(question), question


def test_explain_again_reuses_the_previous_routed_evidence(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    conversation = client.post(
        "/api/v1/repositories/repo_structural/conversations", headers=HEADERS, json={}
    ).json()["id"]
    url = f"/api/v1/conversations/{conversation}/messages"
    first = client.post(
        url, headers=HEADERS, json={"content": "If I change src/format.ts what breaks?"}
    )
    assert first.status_code == 201, first.text
    first_answer = first.json()["assistant_message"]["answer"]
    assert first_answer["evidence_ids"]
    again = client.post(url, headers=HEADERS, json={"content": "explain that again"})
    answer = again.json()["assistant_message"]["answer"]
    assert answer["evidence_ids"] == first_answer["evidence_ids"]
    assert any("previous question" in item for item in answer["limitations"])
    assert "No supporting evidence" not in answer["answer"]


def test_a_follow_up_without_history_still_refuses_honestly(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    conversation = client.post(
        "/api/v1/repositories/repo_structural/conversations", headers=HEADERS, json={}
    ).json()["id"]
    reply = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers=HEADERS,
        json={"content": "explain that again"},
    )
    answer = reply.json()["assistant_message"]["answer"]
    assert not any("previous question" in item for item in answer["limitations"])


def test_project_overview_requests_in_hinglish_and_hindi_are_not_follow_ups() -> None:
    from code_genome_api.services.question_routing import is_overview_question

    for question in (
        "ye project samjhao",
        "is project ke baare mein batao",
        "yeh app kya karta hai",
        "explain this repo",
        "इस प्रोजेक्ट को समझाओ",
    ):
        assert is_overview_question(question, "acme/widget"), question
        assert not is_follow_up(question), question


def test_overview_after_another_question_explains_the_project(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    conversation = client.post(
        "/api/v1/repositories/repo_structural/conversations", headers=HEADERS, json={}
    ).json()["id"]
    url = f"/api/v1/conversations/{conversation}/messages"
    client.post(url, headers=HEADERS, json={"content": "If I change src/format.ts what breaks?"})
    overview = client.post(url, headers=HEADERS, json={"content": "ye project samjhao"})
    answer = overview.json()["assistant_message"]["answer"]
    assert "Fixture Pay" in answer["answer"]
    assert not any("previous question" in item for item in answer["limitations"])
