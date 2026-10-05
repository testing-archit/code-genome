import subprocess
from pathlib import Path
from typing import Any

import pytest
from code_genome_api.models import AnalysisRun, BugLink, Membership, Provenance, Workspace
from code_genome_api.services.genome import semantic_pairs
from code_genome_api.services.structural_analysis import run_structural_analysis
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from test_structural_analysis import FixtureCloner, git, seed_analysis

HEADERS = {"X-Workspace-ID": "ws_structural", "X-User-ID": "usr_structural"}
OUTSIDER = {"X-Workspace-ID": "ws_other", "X-User-ID": "usr_other"}
BASE = "/api/v1/repositories/repo_structural"

FILES = {
    "src/api/users.ts": (
        'import express from "express";\n'
        'import { listUsers } from "../services/users";\n'
        "export const router = express.Router();\n"
        "export function handleList() { return listUsers(); }\n"
    ),
    "src/services/users.ts": (
        'import { db } from "../db/client";\n'
        'import Stripe from "stripe";\n'
        "export const stripe = new Stripe('key');\n"
        "export function listUsers() {\n"
        "  return db.user.findMany({ where: { deleted: false } });\n"
        "}\n"
        "export function addUser(name: string) { return db.user.create({ data: { name } }); }\n"
        'export function ping() { return fetch("https://status.example.com/health"); }\n'
    ),
    "src/db/client.ts": (
        'import { PrismaClient } from "@prisma/client";\nexport const db = new PrismaClient();\n'
    ),
    "src/components/Button.tsx": (
        'import React from "react";\nexport function Button() { return <button />; }\n'
    ),
    "src/utils/format.ts": "export function format(value: string) { return value.trim(); }\n",
}


def commit(worktree: Path, message: str, author: str) -> str:
    git(worktree, "add", ".")
    subprocess.run(
        [
            "git",
            "-c",
            f"user.name={author}",
            "-c",
            f"user.email={author.split()[0].lower()}@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return git(worktree, "rev-parse", "HEAD")


def create_genome_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    worktree = tmp_path / "genome"
    worktree.mkdir()
    git(worktree, "init", "-b", "main")
    for path, content in FILES.items():
        (worktree / path).parent.mkdir(parents=True, exist_ok=True)
        (worktree / path).write_text(content)
    shas = {"initial": commit(worktree, "initial import", "Ada Lovelace")}
    users = worktree / "src/services/users.ts"
    users.write_text(
        users.read_text().replace("{ where: { deleted: false } }", "{ where: {} }"),
    )
    shas["introducing"] = commit(worktree, "simplify user listing query", "Bug Author")
    users.write_text(
        users.read_text().replace("{ where: {} }", "{ where: { deleted: false } }"),
    )
    shas["fix"] = commit(worktree, "fix: user listing returned deleted users", "Ada Lovelace")
    bare = tmp_path / "genome.git"
    git(tmp_path, "clone", "--bare", str(worktree), str(bare))
    return bare, shas


@pytest.fixture
def genome_repo(
    client: TestClient, session_factory: sessionmaker[Session], tmp_path: Path
) -> dict[str, str]:
    bare, shas = create_genome_fixture(tmp_path)
    seed_analysis(session_factory)
    with session_factory() as db:
        db.add(Workspace(id="ws_other", name="Other"))
        db.add(Membership(workspace_id="ws_other", user_id="usr_other", role="owner"))
        db.commit()
    run_structural_analysis("run_structural", session_factory, FixtureCloner(bare))
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_structural")
        assert run is not None and run.state == "SUCCEEDED", run and run.error_detail
    shas["bare"] = str(bare)
    return shas


def _get(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    response = client.get(url, headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_analysis_traces_bug_links_and_reports_progress(
    genome_repo: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        run = db.get(AnalysisRun, "run_structural")
        assert run is not None
        assert run.progress_counts["fix_commits"] == 1
        assert run.progress_counts["bug_links_traced"] >= 1
        assert run.progress_counts["commits_mined"] == 3
        assert any("candidate bug-introducing links" in line for line in run.diagnostics)
        link = db.scalar(
            select(BugLink).where(BugLink.introducing_sha == genome_repo["introducing"])
        )
        assert link is not None
        assert link.fix_sha == genome_repo["fix"]
        assert link.path == "src/services/users.ts"
        assert link.workspace_id == "ws_structural"
        assert link.analysis_version == "szz-lite@1"
        assert link.lines == 1 and 0 < link.confidence < 1
        assert link.evidence_json["blamed_ranges"] == [[5, 5]]
        provenance = db.get(Provenance, link.provenance_id)
        assert provenance is not None and provenance.kind == "szz_blame"
        assert provenance.repository_sha == link.evidence_json["parent_sha"]


def test_reanalysis_backfills_bug_links_idempotently(
    genome_repo: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        before = db.scalar(select(func.count()).select_from(BugLink))
        db.execute(delete(BugLink))
        db.commit()
    seed_analysis(session_factory, "run_backfill")
    run_structural_analysis(
        "run_backfill", session_factory, FixtureCloner(Path(genome_repo["bare"]))
    )
    seed_analysis(session_factory, "run_again")
    run_structural_analysis("run_again", session_factory, FixtureCloner(Path(genome_repo["bare"])))
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(BugLink)) == before
        backfill = db.get(AnalysisRun, "run_backfill")
        again = db.get(AnalysisRun, "run_again")
        assert backfill is not None and again is not None
        assert any("candidate bug links" in line for line in backfill.diagnostics)
        assert again.diagnostics == [
            "Reused the existing immutable snapshot for this repository commit."
        ]


def test_bugs_endpoint_lists_fixes_with_candidates(
    genome_repo: dict[str, str], client: TestClient
) -> None:
    body = _get(client, f"{BASE}/bugs")
    assert body["analysis_version"] == "szz-lite@1"
    assert [fix["fix_sha"] for fix in body["fixes"]] == [genome_repo["fix"]]
    fix = body["fixes"][0]
    assert fix["subject"].startswith("fix: user listing")
    assert "src/services/users.ts" in fix["files"]
    candidate = next(
        item for item in fix["introducing"] if item["introducing_sha"] == genome_repo["introducing"]
    )
    assert candidate["author"] == "Bug Author"
    assert candidate["evidence_id"].startswith("evidence:")
    assert candidate["lines"] == 1
    assert body["files"][0]["path"] == "src/services/users.ts"
    assert any("heuristic" in item for item in body["limitations"])

    scoped = _get(client, f"{BASE}/bugs", path="src/utils/format.ts")
    assert scoped["fixes"] == [] and scoped["files"] == []
    assert (
        scoped["limitations"][0] == "No supporting evidence was identified in the selected scope."
    )
    assert client.get(f"{BASE}/bugs", headers=HEADERS, params={"path": "../x"}).status_code == 422
    assert client.get(f"{BASE}/bugs", headers=OUTSIDER).status_code == 404


def test_genome_graph_shape_and_evidence(genome_repo: dict[str, str], client: TestClient) -> None:
    body = _get(client, f"{BASE}/genome")
    assert body["scope"]["snapshot_sha"]
    kinds = {node["kind"] for node in body["nodes"]}
    assert {"file", "component", "developer", "commit", "external", "datastore"} <= kinds
    assert "external_api" in kinds
    assert "function" not in kinds  # symbols only appear when focused
    edges = body["edges"]
    edge_kinds = {edge["kind"] for edge in edges}
    assert {
        "IMPORTS",
        "CALLS",
        "DEPENDS_ON",
        "BELONGS_TO_MODULE",
        "MODIFIED_BY",
        "OWNED_BY",
        "INTRODUCED_BUG",
        "FIXED_BY",
        "CALLS_API",
        "AUTHORED_BY",
    } <= edge_kinds
    assert edge_kinds & {"READS_FROM", "WRITES_TO", "USES_DATASTORE"}
    node_ids = {node["id"] for node in body["nodes"]}
    for edge in edges:
        assert edge["source"] in node_ids and edge["target"] in node_ids
        assert isinstance(edge["inferred"], bool)
        if edge["kind"] not in {"BELONGS_TO_MODULE", "OWNED_BY"}:
            assert edge["evidence_ids"], edge
        assert all(item.startswith(("evidence:", "commit:")) for item in edge["evidence_ids"])
    introduced = next(edge for edge in edges if edge["kind"] == "INTRODUCED_BUG")
    assert introduced["inferred"] is True
    assert introduced["source"] == f"commit:{genome_repo['introducing']}"
    assert introduced["target"] == "file:src/services/users.ts"
    calls = [edge for edge in edges if edge["kind"] == "CALLS"]
    assert all(edge["inferred"] and edge["confidence"] < 1 for edge in calls)
    assert any(
        edge["source"] == "file:src/api/users.ts" and edge["target"] == "file:src/services/users.ts"
        for edge in calls
    )
    store = next(node for node in body["nodes"] if node["kind"] == "datastore")
    assert store["label"] == "Prisma (SQL database)" and store["inferred"] is True
    file_node = next(node for node in body["nodes"] if node["id"] == "file:src/services/users.ts")
    assert file_node["properties"]["loc"] >= 6
    assert file_node["properties"]["complexity"] >= 1
    assert body["node_counts"]["file"] == 5
    assert not any("@" in node["id"] for node in body["nodes"] if node["kind"] == "developer")


def test_genome_focus_returns_neighbourhood_with_symbols(
    genome_repo: dict[str, str], client: TestClient
) -> None:
    body = _get(client, f"{BASE}/genome", focus="src/api/users.ts", limit=50)
    assert body["focus_node_id"] == "file:src/api/users.ts"
    kinds = {node["kind"] for node in body["nodes"]}
    assert "function" in kinds
    assert len(body["nodes"]) <= 50
    symbol_calls = [
        edge
        for edge in body["edges"]
        if edge["kind"] == "CALLS" and edge["source"].startswith("symbol:")
    ]
    assert symbol_calls
    component = _get(client, f"{BASE}/genome", focus="src/services")
    assert component["focus_node_id"] == "component:src/services"
    missing = client.get(f"{BASE}/genome", headers=HEADERS, params={"focus": "nope/nothing"})
    assert missing.status_code == 404
    assert client.get(f"{BASE}/genome", headers=OUTSIDER).status_code == 404


def test_module_graph_roles_stores_integrations_and_history(
    genome_repo: dict[str, str], client: TestClient
) -> None:
    graph = _get(client, f"{BASE}/module-graph")
    nodes = {node["name"]: node for node in graph["nodes"]}
    assert nodes["src/api"]["role"] == "api"
    assert nodes["src/api"]["role_signal"] == "path segment 'api'"
    assert nodes["src/services"]["role"] == "service"
    assert nodes["src/db"]["role"] == "data"
    assert nodes["src/components"]["role"] == "ui"
    assert nodes["src/utils"]["role"] == "util"
    db_store = nodes["src/db"]["datastores"][0]
    assert db_store["name"] == "Prisma (SQL database)" and db_store["via"] == "direct"
    service_store = nodes["src/services"]["datastores"][0]
    assert service_store["via"] == "via import"
    assert service_store["access"] == "read_write"
    integrations = {item["name"] for item in nodes["src/services"]["integrations"]}
    assert integrations == {"Stripe", "status.example.com"}
    services = nodes["src/services"]
    assert services["commits"] == 3 and services["bug_fixes"] == 1
    assert services["last_changed"] is not None
    assert services["contributors"][0]["name"] == "Ada Lovelace"
    assert services["contributors"][0]["share"] == pytest.approx(2 / 3, abs=1e-3)

    docs = _get(client, f"{BASE}/docs")
    documents = {item["name"]: item["markdown"] for item in docs["documents"]}
    architecture = documents["ARCHITECTURE.md"]
    assert "## Architecture by role" in architecture
    assert architecture.index("API and entry layer") < architecture.index("Data access")
    assert "Prisma (SQL database)" in architecture and "Stripe" in architecture
    flow = documents["DATA_FLOW.md"]
    assert "## Flows into data stores and integrations" in flow
    assert "`src/api` [api] → `src/services` [service]" in flow


def test_timeline_buckets_components_and_fixes(
    genome_repo: dict[str, str], client: TestClient
) -> None:
    body = _get(client, f"{BASE}/timeline", bucket="month")
    assert body["bucket"] == "month"
    assert len(body["buckets"]) == 1
    overall = body["overall"][0]
    assert overall["commits"] == 3
    assert overall["fix_commits"] == 1
    assert overall["authors"] == 2
    assert overall["bug_introducing_commits"] == 1
    services = next(item for item in body["components"] if item["name"] == "src/services")
    assert services["role"] == "service" and services["points"][0]["commits"] == 3
    weekly = _get(client, f"{BASE}/timeline", path="src/utils/format.ts")
    assert weekly["components"] == [] and weekly["overall"][0]["commits"] == 1
    bad = client.get(f"{BASE}/timeline", headers=HEADERS, params={"bucket": "day"})
    assert bad.status_code == 422
    assert client.get(f"{BASE}/timeline", headers=OUTSIDER).status_code == 404


def test_semantic_pairs_are_deterministic_and_thresholded() -> None:
    documents = {
        "src/billing/invoice.ts": "billing invoice create invoice total",
        "src/billing/invoice_total.ts": "billing invoice total invoice",
        "src/auth/login.ts": "auth login session token",
        "src/auth/session.ts": "auth session token refresh",
        "src/ui/button.tsx": "ui button click",
    }
    first = semantic_pairs(documents)
    assert first == semantic_pairs(dict(reversed(list(documents.items()))))
    pairs = {(left, right) for left, right, _ in first}
    assert ("src/billing/invoice.ts", "src/billing/invoice_total.ts") in pairs
    assert all(score >= 0.6 for _, _, score in first)
    assert semantic_pairs({"a.ts": "x"}) == []
