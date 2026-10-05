import json
from pathlib import Path
from typing import Any

import pytest
from code_genome_api.models import AnalysisRun, KnowledgeChunkRecord, Provenance
from code_genome_api.services.knowledge import chunk_file, classify
from code_genome_api.services.question_routing import (
    is_overview_question,
    mentioned_files,
    resolve_path,
)
from code_genome_api.services.structural_analysis import run_structural_analysis
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from test_conversations_voice import FakeResponse, configured_settings
from test_structural_analysis import FixtureCloner, git, seed_analysis

HEADERS = {"X-Workspace-ID": "ws_structural", "X-User-ID": "usr_structural"}
README = """# Fixture Pay

Fixture Pay is a tiny invoicing service that formats invoice totals for small shops.

## Install

```
npm install fixture-pay
# this hash line is code, not a heading
```

## Usage

Call formatTotal with an amount in cents.
"""


def create_knowledge_fixture(tmp_path: Path) -> Path:
    worktree = tmp_path / "knowledge"
    (worktree / "src").mkdir(parents=True)
    (worktree / "docs").mkdir()
    (worktree / "node_modules" / "left-pad").mkdir(parents=True)
    git(worktree, "init", "-b", "main")
    git(worktree, "config", "user.name", "Fixture Author")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    (worktree / "README.md").write_text(README)
    (worktree / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture-pay",
                "description": "Invoice formatting for small shops",
                "scripts": {"test": "vitest"},
                "dependencies": {"zod": "^3.0.0"},
            }
        )
    )
    (worktree / "docs" / "architecture.md").write_text(
        "# Architecture\n\nTotals flow from src/index.ts into src/format.ts.\n"
    )
    (worktree / "src" / "format.ts").write_text(
        "export function formatTotal(cents: number) {\n"
        "  return `$${(cents / 100).toFixed(2)}`;\n}\n"
    )
    (worktree / "src" / "index.ts").write_text(
        "import { formatTotal } from './format';\n"
        "export const API_KEY = 'sk-live-0123456789abcdef';\n"
        "export const render = (cents: number) => formatTotal(cents);\n"
    )
    (worktree / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1;\n")
    git(worktree, "add", "-f", ".")
    git(worktree, "commit", "-m", "add invoice formatting")
    bare = tmp_path / "knowledge.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return bare


@pytest.fixture
def analysed(client: TestClient, session_factory: sessionmaker[Session], tmp_path: Path) -> Path:
    bare = create_knowledge_fixture(tmp_path)
    seed_analysis(session_factory)
    run_structural_analysis("run_structural", session_factory, FixtureCloner(bare))
    return bare


def _ask(client: TestClient, question: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/chat/answers",
        headers=HEADERS,
        json={"repository_id": "repo_structural", "question": question, "channel": "voice"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_classify_and_chunk_are_deterministic_and_safe() -> None:
    assert classify("README.md") == "readme"
    assert classify("backend/package.json") == "manifest"
    assert classify("docs/guide.md") == "doc"
    assert classify("contracts/Token.sol") == "source"
    for skipped in ("node_modules/x/index.js", "dist/app.js", "package-lock.json", "logo.png"):
        assert classify(skipped) is None

    chunks = chunk_file("README.md", "readme", README.encode())
    assert [chunk.heading for chunk in chunks] == ["Fixture Pay", "Install", "Usage"]
    assert chunks[1].start_line == 5 and "this hash line is code" in chunks[1].text
    assert chunk_file("bin.dat", "source", b"\x00\x01binary") == []
    secret = chunk_file("src/a.ts", "source", b"const password = 'hunter2hunter2';\n")
    assert "hunter2" not in secret[0].text and "[REDACTED]" in secret[0].text


def test_question_routing_rules() -> None:
    assert is_overview_question("What does this project do?")
    assert is_overview_question("ye project kya karta hai?")
    assert is_overview_question("यह प्रोजेक्ट क्या करता है?")
    assert is_overview_question("What is fixture-pay for?", "acme/fixture-pay")
    assert not is_overview_question("What changed recently in this project?")
    assert not is_overview_question("What does formatTotal in src/format.ts do?")
    assert mentioned_files("if i change in app.tsx file what can be break") == ["app.tsx"]
    assert mentioned_files("agar main source/index.ts badlu toh kya tootega") == ["source/index.ts"]
    assert mentioned_files("Show me src/format.ts") == []

    paths = ["src/App.tsx", "src/app/routes.ts", "frontend/src/main.tsx"]
    assert resolve_path(paths, "app.tsx") == (["src/App.tsx"], [])
    assert resolve_path(paths, "src/App.tsx") == (["src/App.tsx"], [])
    matches, suggestions = resolve_path(paths, "mian.tsx")
    assert matches == [] and "frontend/src/main.tsx" in suggestions


def test_analysis_stores_cited_redacted_knowledge(
    analysed: Path, client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        chunks = list(db.scalars(select(KnowledgeChunkRecord)))
        run = db.get(AnalysisRun, "run_structural")
    paths = {chunk.path for chunk in chunks}
    assert {"README.md", "package.json", "docs/architecture.md", "src/index.ts"} <= paths
    assert not any(path.startswith("node_modules/") for path in paths)
    assert all(chunk.workspace_id == "ws_structural" for chunk in chunks)
    index = next(chunk for chunk in chunks if chunk.path == "src/index.ts")
    assert "sk-live" not in index.text and "[REDACTED]" in index.text
    manifest = next(chunk for chunk in chunks if chunk.path == "package.json")
    assert "Invoice formatting for small shops" in manifest.text
    assert run is not None and any("knowledge chunks" in line for line in run.diagnostics)

    readme = next(chunk for chunk in chunks if chunk.path == "README.md")
    evidence = client.get(f"/api/v1/evidence/{readme.provenance_id}", headers=HEADERS)
    assert evidence.status_code == 200
    assert evidence.json()["path"] == "README.md" and evidence.json()["start_line"] == 1
    hidden = client.get(
        f"/api/v1/evidence/{readme.provenance_id}",
        headers={"X-Workspace-ID": "ws_other", "X-User-ID": "usr_other"},
    )
    assert hidden.status_code in {401, 404}


def test_reanalysing_the_same_commit_backfills_knowledge(
    analysed: Path, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        db.execute(delete(KnowledgeChunkRecord))
        db.execute(delete(Provenance).where(Provenance.kind == "knowledge"))
        db.commit()
    seed_analysis(session_factory, "run_backfill")
    run_structural_analysis("run_backfill", session_factory, FixtureCloner(analysed))
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_backfill")
        count = db.scalar(select(func.count()).select_from(KnowledgeChunkRecord))
    assert run is not None and run.state == "SUCCEEDED"
    assert count and any("Added" in line for line in run.diagnostics)


def test_overview_question_is_answered_from_the_readme(analysed: Path, client: TestClient) -> None:
    answer = _ask(client, "What does this project do?")
    assert "tiny invoicing service" in answer["answer"]
    with_paths = {
        client.get(f"/api/v1/evidence/{item.split(':', 1)[1]}", headers=HEADERS).json()["path"]
        for item in answer["evidence_ids"]
        if item.startswith("evidence:")
    }
    assert "README.md" in with_paths and "package.json" in with_paths


def test_change_questions_use_impact_and_report_missing_files(
    analysed: Path, client: TestClient
) -> None:
    found = _ask(client, "If I change format.ts what can break?")
    assert "src/index.ts" in found["answer"] and found["evidence_ids"]

    missing = _ask(client, "if i change in app.tsx file what can be break")
    assert missing["answer"].startswith("No supporting evidence was identified")
    assert "No file named app.tsx" in missing["answer"]
    assert "There are no .tsx files" in missing["answer"] and "src/" in missing["answer"]
    assert missing["evidence_ids"] == []


def test_code_questions_retrieve_source_excerpts(analysed: Path, client: TestClient) -> None:
    answer = _ask(client, "How is the invoice total formatted with toFixed?")
    assert any(item.startswith("evidence:") for item in answer["evidence_ids"])
    assert "toFixed" in answer["answer"]


def test_voice_session_carries_a_repository_brief(
    analysed: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        observed["body"] = json.loads(request.data)
        return FakeResponse({"name": "auth_tokens/ephemeral-brief"})

    monkeypatch.setattr("code_genome_api.services.gemini_live.urlopen", fake_urlopen)
    monkeypatch.setattr("code_genome_api.routes.voice.get_settings", configured_settings)
    response = client.post(
        "/api/v1/voice/sessions",
        headers=HEADERS,
        json={"repository_id": "repo_structural", "language": "hinglish"},
    )
    assert response.status_code == 201
    instruction = observed["body"]["bidiGenerateContentSetup"]["systemInstruction"]["parts"][0][
        "text"
    ]
    assert "<brief>" in instruction and "tiny invoicing service" in instruction
    assert "fixture-pay" in instruction and "Top-level folders" in instruction
    assert "sk-live" not in instruction
    assert "ye project" in instruction and "app.tsx" in instruction


def test_why_risky_questions_resolve_named_files() -> None:
    from code_genome_api.services.question_routing import (  # noqa: PLC0415
        _asks_why_risky,
        risk_question_targets,
    )

    paths = ["src/payments/PaymentService.ts", "src/index.ts", "backend/src/server.ts"]
    assert _asks_why_risky("Why is PaymentService high risk?")
    assert _asks_why_risky("server.ts itna risky kyun hai?")
    assert not _asks_why_risky("What does PaymentService do?")
    assert risk_question_targets("Why is PaymentService high risk?", paths) == [
        "src/payments/PaymentService.ts"
    ]
    assert risk_question_targets("server.ts itna risky kyun hai?", paths) == [
        "backend/src/server.ts"
    ]
    assert risk_question_targets("why is index risky", paths) == []


def test_why_risky_question_cites_hotspot_and_fix_history(
    analysed: Path, client: TestClient
) -> None:
    answer = _ask(client, "Why is format.ts risky?")
    assert answer["evidence_ids"]
    assert "format.ts" in answer["answer"]
