from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from code_genome_api.models import AuditEvent
from code_genome_api.services.insights import SnapshotFacts, component_history, components
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_project_knowledge import HEADERS, analysed  # noqa: F401 - pytest fixture

OUTSIDER = {"X-Workspace-ID": "ws_other", "X-User-ID": "usr_other"}
BASE = "/api/v1/repositories/repo_structural"


def test_components_pick_a_readable_directory_depth() -> None:
    paths = [
        *(f"app/src/pages/p{index}.tsx" for index in range(6)),
        *(f"app/src/components/c{index}.tsx" for index in range(6)),
        *(f"api/src/routes/r{index}.ts" for index in range(5)),
        *(f"api/src/db/d{index}.ts" for index in range(4)),
        "index.ts",
    ]
    groups = set(components(paths).values())
    assert {"app/src/pages", "app/src/components", "api/src/routes", "api/src/db"} <= groups
    assert "(root)" in groups
    assert components([]) == {}


def test_component_history_does_not_count_merge_commits_as_fixes() -> None:
    """Bug-fix counts follow the documented rule: keyword subject, merge commits excluded."""
    when = datetime(2026, 9, 1, tzinfo=UTC)

    def commit(sha: str, message: str, parents: int) -> Any:
        return SimpleNamespace(
            sha=sha,
            message=message,
            parent_shas=[f"{index}" * 40 for index in range(parents)],
            author_email="dev@example.com",
            author_name="Dev",
            authored_at=when,
        )

    commits = [
        commit("a" * 40, "fix: stop double charging", 1),
        commit("b" * 40, "Merge pull request #7 from acme/fix-login", 2),
        commit("c" * 40, "add invoice export", 1),
    ]
    facts = cast(
        SnapshotFacts,
        SimpleNamespace(
            commits=commits,
            file_changes=[
                SimpleNamespace(commit_sha=item.sha, path="src/billing/pay.ts") for item in commits
            ],
            component_of={"src/billing/pay.ts": "src/billing"},
        ),
    )
    history = component_history(facts)["src/billing"]
    assert history["commits"] == 3
    assert history["bug_fixes"] == 1


def test_overview_reports_health_counts_and_summary(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    response = client.get(f"{BASE}/overview", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    health = body["health"]
    assert 0 <= health["score"] <= 100
    assert health["band"] in {"Healthy", "Moderate", "At risk"}
    assert round(sum(item["score"] for item in health["components"])) == health["score"]
    assert {item["key"] for item in health["components"]} == {
        "risk",
        "coupling",
        "ownership",
        "docs",
        "tests",
    }
    assert body["counts"]["contributors"] == 1 and body["counts"]["commits"] == 1
    assert body["counts"]["source_files"] == 2
    assert "tiny invoicing service" in body["summary"]
    assert body["summary_evidence_id"].startswith("evidence:")
    assert body["contributors"][0]["name"] == "Fixture Author"
    assert any("heuristic" in item for item in body["limitations"])
    assert client.get(f"{BASE}/overview", headers=OUTSIDER).status_code in {401, 404}


def test_module_graph_and_generated_documents(
    analysed: Path,  # noqa: F811
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    graph = client.get(f"{BASE}/module-graph", headers=HEADERS).json()
    assert [node["name"] for node in graph["nodes"]] == ["src"]
    assert graph["nodes"][0]["files"] == 2

    docs = client.get(f"{BASE}/docs", headers=HEADERS)
    assert docs.status_code == 200
    documents = {item["name"]: item["markdown"] for item in docs.json()["documents"]}
    assert list(documents) == [
        "ARCHITECTURE.md",
        "MODULES.md",
        "DATA_FLOW.md",
        "DEPENDENCIES.md",
        "BUSINESS_LOGIC.md",
        "RISK_REPORT.md",
    ]
    sha = docs.json()["snapshot_sha"]
    assert all(sha in markdown for markdown in documents.values())
    assert "tiny invoicing service" in documents["ARCHITECTURE.md"]
    assert "`src/index.ts` imports 1 files" in documents["ARCHITECTURE.md"]
    assert "`src/format.ts` (evidence:" in documents["DATA_FLOW.md"]
    assert "Runtime data flow" in documents["DATA_FLOW.md"]
    assert "formatTotal" in documents["MODULES.md"]
    assert "**Install**" in documents["BUSINESS_LOGIC.md"]
    assert "Health score" in documents["RISK_REPORT.md"]
    assert "sk-live" not in "".join(documents.values())

    download = client.get(f"{BASE}/docs/RISK_REPORT.md/download", headers=HEADERS)
    assert download.status_code == 200
    assert 'filename="RISK_REPORT.md"' in download.headers["content-disposition"]
    assert client.get(f"{BASE}/docs/SECRETS.md/download", headers=HEADERS).status_code == 404
    assert client.get(f"{BASE}/docs", headers=OUTSIDER).status_code in {401, 404}
    with session_factory() as db:
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "repository.docs.downloaded")
        )
    assert audit is not None and audit.after_hash == "RISK_REPORT.md"


def test_inventory_accepts_the_file_limit_the_web_app_requests(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    # Regression: the file explorer asks for 5000 files; a 2000 cap returned 422.
    response = client.get(
        f"{BASE}/inventory", headers=HEADERS, params={"commit_limit": 500, "file_limit": 5000}
    )
    assert response.status_code == 200
    assert {item["path"] for item in response.json()["files"]} >= {"README.md", "src/index.ts"}


def test_python_frameworks_and_clients_are_recognised() -> None:
    from collections import Counter

    from code_genome_api.services.insights import (
        DATASTORE_PACKAGES,
        INTEGRATION_PACKAGES,
        _role_for,
    )

    assert _role_for(
        "backend/gateway", ["backend/gateway/main.py"], Counter({"fastapi": 3}), False
    ) == (
        "api",
        "imports fastapi",
    )
    assert _role_for("backend/store", ["backend/store/x.py"], Counter(), True)[0] == "data"
    assert DATASTORE_PACKAGES["sqlalchemy"].startswith("SQL database")
    assert DATASTORE_PACKAGES["psycopg"] == "PostgreSQL"
    assert INTEGRATION_PACKAGES["anthropic"] == "Anthropic"
