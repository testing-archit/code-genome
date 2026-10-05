import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from code_genome_api.config import get_settings
from code_genome_api.models import (
    AnalysisRun,
    AuditEvent,
    CoChangeEdge,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    Membership,
    ModuleCandidate,
    Provenance,
    RepositorySnapshot,
    Workspace,
)
from code_genome_api.services.diffs import DiffError, parse_unified_diff
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

WS = "ws_changes"
USER = "usr_changes"
BASE = "a" * 40
HEAD = "c" * 40
SECRET = "test-webhook-secret-0123456789"


def auth(workspace_id: str = WS, user_id: str = USER) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-User-ID": user_id}


def setup_workspace(
    client: TestClient, factory: sessionmaker[Session], name: str = "acme/changes"
) -> str:
    with factory() as db:
        if db.get(Workspace, WS) is None:
            db.add(Workspace(id=WS, name="Changes"))
            db.add(Membership(workspace_id=WS, user_id=USER, role="owner"))
            db.add(Membership(workspace_id=WS, user_id="usr_viewer", role="member"))
            db.add(Workspace(id="ws_other", name="Other"))
            db.add(Membership(workspace_id="ws_other", user_id="usr_other", role="owner"))
            db.commit()
    created = client.post(
        "/api/v1/repositories",
        headers={**auth(), "Idempotency-Key": f"repo-{name}"},
        json={"clone_url": f"https://github.com/{name}", "default_branch": "main"},
    )
    assert created.status_code == 201
    return cast(str, created.json()["id"])


def seed_snapshot(
    factory: sessionmaker[Session],
    repository_id: str,
    sha: str,
    files: dict[str, str],
    imports: list[tuple[str, str]],
    modules: dict[str, list[str]],
    hotspots: dict[str, float],
    published_at: datetime,
) -> None:
    key = sha[:6]
    with factory() as db:
        db.add(
            AnalysisRun(
                id=f"run_{key}",
                workspace_id=WS,
                repository_id=repository_id,
                snapshot_sha=sha,
                requested_refs=["main"],
                state="SUCCEEDED",
            )
        )
        db.add(
            RepositorySnapshot(
                id=f"snap_{key}",
                workspace_id=WS,
                repository_id=repository_id,
                commit_sha=sha,
                tree_sha="f" * 40,
                run_id=f"run_{key}",
                analysis_version="structural-genome@0.1.0",
                published_at=published_at,
            )
        )
        db.flush()
        for index, (path, blob) in enumerate(files.items()):
            db.add(
                FileManifestEntry(
                    id=f"man_{key}_{index}",
                    workspace_id=WS,
                    repository_id=repository_id,
                    snapshot_id=f"snap_{key}",
                    path=path,
                    blob_sha=blob * 40,
                    mode="100644",
                    size=100 + index,
                    analyzed=True,
                )
            )
            db.add(
                GraphNode(
                    id=f"node_{key}_{index}",
                    workspace_id=WS,
                    snapshot_id=f"snap_{key}",
                    kind="File",
                    natural_key=path,
                )
            )
        db.flush()
        node_ids = {path: f"node_{key}_{index}" for index, path in enumerate(files)}
        for index, (source, target) in enumerate(imports):
            db.add(
                Provenance(
                    id=f"ev_{key}_{index}",
                    workspace_id=WS,
                    repository_id=repository_id,
                    snapshot_id=f"snap_{key}",
                    kind="import",
                    repository_sha=sha,
                    file_path=source,
                    extractor_version="test@1",
                )
            )
            db.flush()
            db.add(
                GraphEdge(
                    id=f"edge_{key}_{index}",
                    workspace_id=WS,
                    snapshot_id=f"snap_{key}",
                    type="IMPORTS",
                    from_node=node_ids[source],
                    to_node=node_ids[target],
                    confidence=1.0,
                    provenance_id=f"ev_{key}_{index}",
                )
            )
        for name, paths in modules.items():
            db.add(
                ModuleCandidate(
                    id=f"mod_{key}_{name.replace('/', '_')}",
                    workspace_id=WS,
                    repository_id=repository_id,
                    snapshot_id=f"snap_{key}",
                    natural_key=name,
                    file_paths=paths,
                    confidence=0.8,
                    description=f"Inferred module {name}.",
                    evidence_shas=[sha],
                    analysis_version="evolution@1",
                )
            )
        for index, (path, score) in enumerate(hotspots.items()):
            db.add(
                FileHotspot(
                    id=f"hot_{key}_{index}",
                    workspace_id=WS,
                    repository_id=repository_id,
                    snapshot_id=f"snap_{key}",
                    path=path,
                    commit_count=int(score * 10) + 1,
                    churn=int(score * 100) + 1,
                    score=score,
                    evidence_shas=[sha],
                )
            )
        db.commit()


def seed_two_snapshots(factory: sessionmaker[Session], repository_id: str) -> None:
    seed_snapshot(
        factory,
        repository_id,
        BASE,
        {"src/api.ts": "1", "src/billing.ts": "2", "src/legacy.ts": "3"},
        [("src/api.ts", "src/billing.ts"), ("src/api.ts", "src/legacy.ts")],
        {"src": ["src/api.ts", "src/billing.ts", "src/legacy.ts"]},
        {"src/billing.ts": 0.4, "src/api.ts": 0.2},
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    seed_snapshot(
        factory,
        repository_id,
        HEAD,
        {"src/api.ts": "1", "src/billing.ts": "9", "src/ui/card.tsx": "4"},
        [("src/api.ts", "src/billing.ts"), ("src/ui/card.tsx", "src/billing.ts")],
        {"src": ["src/api.ts", "src/billing.ts"], "src/ui": ["src/ui/card.tsx"]},
        {"src/billing.ts": 1.0, "src/api.ts": 0.2},
        datetime(2026, 10, 5, tzinfo=UTC),
    )
    with factory() as db:
        db.add(
            CoChangeEdge(
                id="cc_head",
                workspace_id=WS,
                repository_id=repository_id,
                snapshot_id=f"snap_{HEAD[:6]}",
                left_path="src/billing.ts",
                right_path="src/ui/card.tsx",
                commit_count=3,
                confidence=0.75,
                evidence_shas=[HEAD],
                analysis_version="evolution@1",
            )
        )
        db.commit()


GIT_DIFF = """diff --git a/src/billing.ts b/src/billing.ts
index 1111111..2222222 100644
--- a/src/billing.ts
+++ b/src/billing.ts
@@ -1,2 +1,2 @@
 export function total() {
+  return 1;
--- this removed-looking line is hunk content
diff --git a/src/new.ts b/src/new.ts
new file mode 100644
--- /dev/null
+++ b/src/new.ts
@@ -0,0 +1 @@
+export const x = 1;
"""


def test_diff_parser_handles_git_and_plain_diffs_and_rejects_unsafe_paths() -> None:
    parsed = {item.path: item for item in parse_unified_diff(GIT_DIFF)}
    assert parsed["src/billing.ts"].change == "modified"
    assert (parsed["src/billing.ts"].additions, parsed["src/billing.ts"].deletions) == (1, 1)
    assert parsed["src/new.ts"].change == "added"

    # A hunk header that over-counts must not swallow the next file's header.
    overcounted = GIT_DIFF.replace("@@ -1,2 +1,2 @@", "@@ -1,9 +1,9 @@")
    assert {item.path for item in parse_unified_diff(overcounted)} == {
        "src/billing.ts",
        "src/new.ts",
    }

    renamed = parse_unified_diff(
        "diff --git a/old.ts b/new.ts\nsimilarity index 90%\nrename from old.ts\nrename to new.ts\n"
    )
    assert renamed[0].path == "new.ts" and renamed[0].previous_path == "old.ts"

    deleted = parse_unified_diff("--- a/gone.js\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n")
    assert deleted[0].change == "deleted" and deleted[0].deletions == 2

    for unsafe in ("--- a/../x\n+++ b/../x\n", "+++ /etc/passwd\n", "no diff here"):
        with pytest.raises(DiffError):
            parse_unified_diff(unsafe)


def test_change_impact_ranks_neighbours_with_evidence(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)
    with session_factory() as db:
        db.add(
            GraphNode(
                id="node_react",
                workspace_id=WS,
                snapshot_id=f"snap_{HEAD[:6]}",
                kind="ExternalModule",
                natural_key="external:react",
            )
        )
        db.add(
            Provenance(
                id="ev_react",
                workspace_id=WS,
                repository_id=repository_id,
                snapshot_id=f"snap_{HEAD[:6]}",
                kind="import",
                repository_sha=HEAD,
                file_path="src/billing.ts",
                extractor_version="test@1",
            )
        )
        db.flush()
        db.add(
            GraphEdge(
                id="edge_react",
                workspace_id=WS,
                snapshot_id=f"snap_{HEAD[:6]}",
                type="IMPORTS",
                from_node=f"node_{HEAD[:6]}_1",
                to_node="node_react",
                confidence=1.0,
                provenance_id="ev_react",
            )
        )
        db.commit()

    single = client.get(
        f"/api/v1/repositories/{repository_id}/impact",
        headers=auth(),
        params={"path": "src/billing.ts"},
    )
    assert single.status_code == 200
    assert all(not item["path"].startswith("external:") for item in single.json()["impacted"])

    response = client.post(
        f"/api/v1/repositories/{repository_id}/impact/change",
        headers=auth(),
        json={"diff": GIT_DIFF, "paths": ["src/api.ts"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_sha"] == HEAD
    assert body["summary"]["changed_files"] == 3
    changed = {item["path"]: item for item in body["changed"]}
    assert changed["src/billing.ts"]["in_snapshot"] is True
    assert changed["src/billing.ts"]["risk_score"] is not None
    assert changed["src/billing.ts"]["modules"] == ["src"]
    assert changed["src/new.ts"]["in_snapshot"] is False
    assert changed["src/api.ts"]["change"] == "listed"
    impacted = {item["path"]: item for item in body["impacted"]}
    assert "src/billing.ts" not in impacted
    assert "external:react" not in impacted
    card = impacted["src/ui/card.tsx"]
    assert card["via"] == ["src/billing.ts"]
    assert card["evidence_ids"]
    assert card["modules"] == ["src/ui"]
    assert any("not in snapshot" in item for item in body["limitations"])


def test_change_impact_rejects_bad_input_and_other_tenants(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)
    url = f"/api/v1/repositories/{repository_id}/impact/change"

    assert client.post(url, headers=auth(), json={}).json()["code"] == "INVALID_SCOPE"
    bad_diff = client.post(url, headers=auth(), json={"diff": "--- a/../x\n+++ b/../x\n"})
    assert bad_diff.status_code == 400 and bad_diff.json()["code"] == "INVALID_DIFF"
    bad_path = client.post(url, headers=auth(), json={"paths": ["../../etc/passwd"]})
    assert bad_path.status_code == 422
    hidden = client.post(url, headers=auth("ws_other", "usr_other"), json={"paths": ["src/a"]})
    assert hidden.status_code == 404


def test_compare_snapshots_reports_files_imports_modules_and_hotspots(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)

    listed = client.get(f"/api/v1/repositories/{repository_id}/snapshots", headers=auth())
    assert [item["commit_sha"] for item in listed.json()] == [HEAD, BASE]
    assert listed.json()[0]["refs"] == ["main"]

    response = client.get(
        f"/api/v1/repositories/{repository_id}/compare",
        headers=auth(),
        params={"base": BASE[:8], "head": HEAD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unavailable"] == []
    assert [item["path"] for item in body["files_added"]] == ["src/ui/card.tsx"]
    assert [item["path"] for item in body["files_removed"]] == ["src/legacy.ts"]
    assert [item["path"] for item in body["files_modified"]] == ["src/billing.ts"]
    assert body["imports_added"] == [
        {
            "source": "src/ui/card.tsx",
            "target": "src/billing.ts",
            "evidence_id": "evidence:ev_cccccc_1",
        }
    ]
    assert [(i["source"], i["target"]) for i in body["imports_removed"]] == [
        ("src/api.ts", "src/legacy.ts")
    ]
    modules = {item["name"]: item for item in body["modules"]}
    assert modules["src"]["status"] == "changed"
    assert modules["src"]["removed_files"] == ["src/legacy.ts"]
    assert modules["src/ui"]["status"] == "added"
    assert body["hotspots"][0] == {
        "path": "src/billing.ts",
        "base_score": 0.4,
        "head_score": 1.0,
        "delta": 0.6,
    }

    same = client.get(
        f"/api/v1/repositories/{repository_id}/compare",
        headers=auth(),
        params={"base": HEAD, "head": HEAD},
    )
    assert same.status_code == 400
    bad = client.get(
        f"/api/v1/repositories/{repository_id}/compare",
        headers=auth(),
        params={"base": "zzzzzzzz", "head": HEAD},
    )
    assert bad.status_code == 400
    hidden = client.get(
        f"/api/v1/repositories/{repository_id}/compare",
        headers=auth("ws_other", "usr_other"),
        params={"base": BASE, "head": HEAD},
    )
    assert hidden.status_code == 404


def test_compare_marks_sections_missing_from_older_snapshots_unavailable(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)
    legacy = "e" * 40
    seed_snapshot(
        session_factory, repository_id, legacy, {}, [], {}, {}, datetime(2026, 9, 1, tzinfo=UTC)
    )
    response = client.get(
        f"/api/v1/repositories/{repository_id}/compare",
        headers=auth(),
        params={"base": legacy, "head": HEAD},
    )
    body = response.json()
    assert body["unavailable"] == ["files", "imports", "modules", "hotspots"]
    assert body["files_added"] == [] and body["counts"]["files_added"] == 0
    assert body["modules"] == [] and body["hotspots"] == []
    assert any("were not compared" in item for item in body["limitations"])


def test_exports_are_cited_and_audited(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)
    base_url = f"/api/v1/repositories/{repository_id}/exports"

    architecture = client.get(f"{base_url}/architecture", headers=auth())
    assert architecture.status_code == 200
    assert architecture.headers["content-type"].startswith("text/markdown")
    assert f"Snapshot: `{HEAD}`" in architecture.text
    assert "## Modules (inferred)" in architecture.text
    assert f"commit:{HEAD}" in architecture.text
    assert "attachment;" in architecture.headers["content-disposition"]

    risk = client.get(f"{base_url}/risk", headers=auth(), params={"format": "json"})
    envelope = risk.json()
    assert envelope["export"]["snapshot"] == HEAD
    assert envelope["data"]["scores"][0]["evidence_ids"]

    comparison = client.get(
        f"{base_url}/comparison", headers=auth(), params={"base": BASE, "head": HEAD}
    )
    assert "src/ui/card.tsx" in comparison.text and "## Limitations" in comparison.text
    missing = client.get(f"{base_url}/comparison", headers=auth())
    assert missing.status_code == 400

    with session_factory() as db:
        actions = list(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.workspace_id == WS, AuditEvent.action.like("repository.export.%")
                )
            )
        )
    assert sorted(actions) == [
        "repository.export.architecture",
        "repository.export.comparison",
        "repository.export.risk",
    ]
    hidden = client.get(f"{base_url}/risk", headers=auth("ws_other", "usr_other"))
    assert hidden.status_code == 404


def _signed(payload: dict[str, object], delivery: str, event: str = "push") -> dict[str, object]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "content": body,
        "headers": {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    }


def test_webhook_requires_configuration_and_valid_signature(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "github_webhook_secret", None)
    request = _signed({"zen": "hi"}, "delivery-1", "ping")
    assert client.post("/api/v1/webhooks/github", **request).status_code == 503  # type: ignore[arg-type]

    monkeypatch.setattr(get_settings(), "github_webhook_secret", SecretStr(SECRET))
    forged = _signed({"zen": "hi"}, "delivery-1", "ping")
    forged["headers"]["X-Hub-Signature-256"] = "sha256=" + "0" * 64  # type: ignore[index]
    rejected = client.post("/api/v1/webhooks/github", **forged)  # type: ignore[arg-type]
    assert rejected.status_code == 401 and rejected.json()["code"] == "INVALID_SIGNATURE"
    pong = client.post("/api/v1/webhooks/github", **request)  # type: ignore[arg-type]
    assert pong.status_code == 200 and pong.json()["outcome"] == "pong"


def test_push_webhook_queues_only_opted_in_repositories_once(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "github_webhook_secret", SecretStr(SECRET))
    repository_id = setup_workspace(client, session_factory, "Acme/Webhooks")
    push = {"ref": "refs/heads/main", "repository": {"full_name": "Acme/Webhooks"}}
    url = "/api/v1/webhooks/github"

    off = client.post(url, **_signed(push, "d-off"))  # type: ignore[arg-type]
    assert off.json()["outcome"] == "ignored"

    viewer = client.put(
        f"/api/v1/repositories/{repository_id}/automation",
        headers=auth(user_id="usr_viewer"),
        json={"auto_analyze": True},
    )
    assert viewer.status_code == 403
    enabled = client.put(
        f"/api/v1/repositories/{repository_id}/automation",
        headers=auth(),
        json={"auto_analyze": True},
    )
    assert enabled.json()["auto_analyze"] is True
    assert enabled.json()["webhook_configured"] is True

    queued = client.post(url, **_signed(push, "d-1"))  # type: ignore[arg-type]
    assert queued.status_code == 202
    assert queued.json()["outcome"] == "queued" and len(queued.json()["queued_run_ids"]) == 1
    replay = client.post(url, **_signed(push, "d-1"))  # type: ignore[arg-type]
    assert replay.json()["outcome"] == "duplicate"
    coalesced = client.post(url, **_signed(push, "d-2"))  # type: ignore[arg-type]
    assert coalesced.json()["outcome"] == "coalesced"
    other_branch = {**push, "ref": "refs/heads/feature"}
    ignored = client.post(url, **_signed(other_branch, "d-3"))  # type: ignore[arg-type]
    assert ignored.json()["outcome"] == "ignored"
    deleted = {**push, "deleted": True}
    assert client.post(url, **_signed(deleted, "d-4")).json()["outcome"] == "ignored"  # type: ignore[arg-type]
    invalid = {"ref": "refs/heads/main", "repository": {"full_name": "../../x"}}
    assert client.post(url, **_signed(invalid, "d-5")).status_code == 422  # type: ignore[arg-type]

    with session_factory() as db:
        runs = list(
            db.scalars(select(AnalysisRun).where(AnalysisRun.repository_id == repository_id))
        )
        audit = db.scalar(select(AuditEvent).where(AuditEvent.actor_id == "system:github-webhook"))
    assert len(runs) == 1 and runs[0].requested_refs == ["main"]
    assert audit is not None and audit.workspace_id == WS


def test_impact_history_is_cached_per_snapshot_and_workspace(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated impact requests reuse one history load; the cache key includes the workspace."""
    from types import SimpleNamespace

    from code_genome_api.services import impact, ml
    from code_genome_ml import TrainingInputs

    impact._cache.clear()
    calls: list[tuple[str, str]] = []
    real = ml.training_inputs

    def counting(db: Session, snapshot: RepositorySnapshot) -> TrainingInputs:
        calls.append((snapshot.workspace_id, snapshot.id))
        return real(db, snapshot)

    monkeypatch.setattr(ml, "training_inputs", counting)
    repository_id = setup_workspace(client, session_factory)
    seed_two_snapshots(session_factory, repository_id)
    url = f"/api/v1/repositories/{repository_id}/impact/change"
    for _ in range(2):
        response = client.post(url, headers=auth(), json={"diff": GIT_DIFF, "paths": []})
        assert response.status_code == 200
    assert calls == [(WS, f"snap_{HEAD[:6]}")]
    for item in response.json()["impacted"]:
        metrics = item["graph_metrics"]
        if metrics is not None:
            assert 0 <= metrics["pagerank_percentile"] <= 1
            assert 0 <= metrics["betweenness"] <= 1

    with session_factory() as db:
        snapshot = db.get(RepositorySnapshot, f"snap_{HEAD[:6]}")
        assert snapshot is not None
        foreign = SimpleNamespace(
            id=snapshot.id,
            repository_id=snapshot.repository_id,
            analysis_version=snapshot.analysis_version,
            workspace_id="ws_other",
        )
        impact._prepare(db, cast(RepositorySnapshot, foreign), None)
    assert calls[-1] == ("ws_other", f"snap_{HEAD[:6]}")
    assert len(calls) == 2
