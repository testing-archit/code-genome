import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from code_genome_api.models import (
    AnalysisRun,
    AuditEvent,
    CoChangeEdge,
    FileChange,
    FileManifestEntry,
    GraphNode,
    Membership,
    RepositoryCommit,
    RepositorySnapshot,
    Workspace,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "ml" / "tests"))
from test_models import BUGGY, synthetic_repository  # noqa: E402

OWNER = {"X-Workspace-ID": "ws_ml", "X-User-ID": "usr_ml"}
INTRUDER = {"X-Workspace-ID": "ws_other", "X-User-ID": "usr_other"}


def seed(client: TestClient, factory: sessionmaker[Session], commits: int = 180) -> str:
    with factory() as db:
        db.add(Workspace(id="ws_ml", name="ML"))
        db.add(Membership(workspace_id="ws_ml", user_id="usr_ml"))
        db.add(Workspace(id="ws_other", name="Other"))
        db.add(Membership(workspace_id="ws_other", user_id="usr_other"))
        db.commit()
    repository_id = cast(
        str,
        client.post(
            "/api/v1/repositories",
            headers={**OWNER, "Idempotency-Key": f"ml-repository-{commits}"},
            json={"clone_url": "https://github.com/acme/ml", "default_branch": "main"},
        ).json()["id"],
    )
    history = synthetic_repository(commits=commits)
    with factory() as db:
        db.add(
            AnalysisRun(
                id="run_ml",
                workspace_id="ws_ml",
                repository_id=repository_id,
                snapshot_sha="c" * 40,
                state="SUCCEEDED",
            )
        )
        db.add(
            RepositorySnapshot(
                id="snap_ml",
                workspace_id="ws_ml",
                repository_id=repository_id,
                commit_sha="c" * 40,
                tree_sha="d" * 40,
                run_id="run_ml",
                analysis_version="test@1",
                published_at=datetime(2026, 12, 1, tzinfo=UTC),
            )
        )
        for index, commit in enumerate(history.commits):
            db.add(
                RepositoryCommit(
                    id=f"cmt_{index}",
                    workspace_id="ws_ml",
                    repository_id=repository_id,
                    sha=commit.sha,
                    parent_shas=["0" * 40],
                    author_name=commit.author,
                    author_email=f"{commit.author}@example.com",
                    authored_at=commit.authored_at,
                    message=commit.message,
                )
            )
        for index, change in enumerate(history.changes):
            db.add(
                FileChange(
                    id=f"chg_{index}",
                    workspace_id="ws_ml",
                    repository_id=repository_id,
                    snapshot_id="snap_ml",
                    commit_sha=change.commit_sha,
                    path=change.path,
                    authored_at=change.authored_at,
                    churn=change.churn,
                )
            )
        for index, record in enumerate(history.files):
            db.add(
                FileManifestEntry(
                    id=f"man_{index}",
                    workspace_id="ws_ml",
                    repository_id=repository_id,
                    snapshot_id="snap_ml",
                    path=record.path,
                    blob_sha=f"{index:040x}",
                    mode="100644",
                    size=record.size,
                    analyzed=True,
                )
            )
        db.commit()
    return repository_id


def test_models_train_and_power_risk_search_and_impact(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = seed(client, session_factory)
    before = client.get(f"/api/v1/repositories/{repository_id}/ml", headers=OWNER).json()
    assert before["trained"] is False
    baseline = client.get(f"/api/v1/repositories/{repository_id}/risk", headers=OWNER).json()
    assert not any(item["model_version"].startswith("defect") for item in baseline["scores"])

    trained = client.post(f"/api/v1/repositories/{repository_id}/ml/train", headers=OWNER)
    assert trained.status_code == 200
    tasks = trained.json()["tasks"]
    assert tasks["commit_intent"]["status"] == "trained"
    assert tasks["defect_risk"]["status"] == "trained"
    assert tasks["anomalies"]["status"] == "trained"
    # Without an import graph, retrieval evaluation and link prediction still run on history.
    assert tasks["change_impact"]["status"] == "trained"
    instability = tasks["instability"]
    assert instability["status"] == "trained", instability["result"].get("reason")
    assert instability["model_version"].startswith("instability-windowed")
    assert {"model", "baseline_persistence"} <= set(instability["result"]["metrics"])
    stored = client.get(f"/api/v1/repositories/{repository_id}/ml", headers=OWNER).json()
    assert stored["tasks"]["instability"]["result"]["predictions"]

    overview = client.get(f"/api/v1/repositories/{repository_id}/overview", headers=OWNER).json()
    unstable = overview["unstable_components"]
    assert unstable and {item["name"] for item in unstable} <= {
        "src/billing",
        "src/auth",
        "src/ui",
        "src/api",
        "src/search",
    }
    assert unstable == sorted(unstable, key=lambda item: -item["probability"])
    assert all(ref.startswith("commit:") for item in unstable for ref in item["evidence_ids"])
    assert any("inferred forecast" in line for line in overview["limitations"])
    docs = client.get(f"/api/v1/repositories/{repository_id}/docs", headers=OWNER).json()
    report = next(item for item in docs["documents"] if item["name"] == "RISK_REPORT.md")
    assert "likely to become unstable next period" in report["markdown"]

    risk = client.get(f"/api/v1/repositories/{repository_id}/risk", headers=OWNER).json()
    assert risk["scores"][0]["model_version"].startswith("defect-temporal@3")
    assert len({item["path"] for item in risk["scores"][:4]} & BUGGY) >= 3
    assert risk["scores"][0]["evidence_ids"][0].startswith("commit:")
    champion = tasks["defect_risk"]["result"]["champion"]
    assert risk["scores"][0]["model_version"].endswith(f":{champion}")
    note = risk["limitations"][-1]
    assert note.startswith(f"Champion model: {champion.replace('_', ' ')}")
    assert tasks["defect_risk"]["result"]["contribution_method"] in note

    impact = client.get(
        f"/api/v1/repositories/{repository_id}/impact",
        params={"path": "src/billing/tax.ts"},
        headers=OWNER,
    ).json()
    assert any(
        "model predicts co-change" in reason
        for item in impact["impacted"]
        for reason in item["reasons"]
    )

    search = client.get(
        f"/api/v1/repositories/{repository_id}/search",
        params={"q": "session token", "kind": "file"},
        headers=OWNER,
    ).json()
    assert search["hits"][0]["path"] in {"src/auth/session.ts", "src/auth/token.ts"}
    empty = client.get(
        f"/api/v1/repositories/{repository_id}/search",
        params={"q": "quantum chromodynamics"},
        headers=OWNER,
    ).json()
    assert empty["hits"] == []
    assert empty["message"] == "No supporting evidence was identified in the selected scope."

    assert (
        client.get(f"/api/v1/repositories/{repository_id}/ml", headers=INTRUDER).status_code == 404
    )
    assert (
        client.post(f"/api/v1/repositories/{repository_id}/ml/train", headers=INTRUDER).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/repositories/{repository_id}/search",
            params={"q": "session"},
            headers=INTRUDER,
        ).status_code
        == 404
    )
    with session_factory() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "ml_models.trained"))


def test_short_history_abstains_and_keeps_baseline_risk(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = seed(client, session_factory, commits=8)
    tasks = client.post(f"/api/v1/repositories/{repository_id}/ml/train", headers=OWNER).json()[
        "tasks"
    ]
    assert tasks["defect_risk"]["status"] == "insufficient_data"
    assert tasks["defect_risk"]["result"]["reason"]
    assert tasks["instability"]["status"] == "insufficient_data"
    assert tasks["instability"]["result"]["reason"]
    overview = client.get(f"/api/v1/repositories/{repository_id}/overview", headers=OWNER).json()
    assert overview["unstable_components"] is None
    risk = client.get(f"/api/v1/repositories/{repository_id}/risk", headers=OWNER).json()
    assert not any(item["model_version"].startswith("defect") for item in risk["scores"])


def test_multisignal_modules_code_metrics_and_weighted_impact(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    repository_id = seed(client, session_factory)
    history = synthetic_repository()
    # FILE nodes carry analyzer code metrics for most files; malformed values must be
    # ignored rather than trusted.
    with session_factory() as db:
        for index, record in enumerate(history.files):
            properties: dict[str, object] = {"path": record.path}
            if index % 5:
                properties.update({"loc": 50 + index * 7, "complexity": 1 + index % 6})
            elif index == 0:
                properties.update({"loc": "1200", "complexity": -3})
            elif index == 5:
                properties.update({"loc": True})
            db.add(
                GraphNode(
                    id=f"node_ml_{index}",
                    workspace_id="ws_ml",
                    snapshot_id="snap_ml",
                    kind="FILE",
                    natural_key=record.path,
                    properties_json=properties,
                )
            )
        db.add(
            CoChangeEdge(
                id="cce_ml_tax_currency",
                workspace_id="ws_ml",
                repository_id=repository_id,
                snapshot_id="snap_ml",
                left_path="src/billing/currency.ts",
                right_path="src/billing/tax.ts",
                commit_count=3,
                confidence=0.4,
                evidence_shas=[],
                analysis_version="test@1",
            )
        )
        db.commit()

    # Before training, impact still explains itself, labelling bug fixes by keyword.
    untrained = client.get(
        f"/api/v1/repositories/{repository_id}/impact",
        params={"path": "src/billing/tax.ts"},
        headers=OWNER,
    ).json()
    assert untrained["impacted"]
    assert all(item["signals"] is not None for item in untrained["impacted"])
    assert any("keyword rule" in line for line in untrained["limitations"])

    tasks = client.post(f"/api/v1/repositories/{repository_id}/ml/train", headers=OWNER).json()[
        "tasks"
    ]
    defect = tasks["defect_risk"]["result"]
    assert tasks["defect_risk"]["model_version"] == "defect-temporal@3"
    code = defect["dataset"]["code_metrics"]
    assert code["used"] == ["loc", "complexity"]
    assert code["missing"] == ["functions"]
    # 20 files; indexes 0, 5, 10 and 15 had no or malformed metrics.
    assert code["files_measured"] == {"loc": 16, "complexity": 16}
    assert {"betweenness", "pagerank", "ownership", "log_loc"} <= set(defect["dataset"]["features"])
    assert {"precision", "recall", "f1", "roc_auc"} <= set(defect["metrics"][defect["champion"]])

    modules = tasks["modules"]
    assert modules["model_version"] == "modules-multisignal@2"
    metrics = modules["result"]["metrics"]
    assert metrics["champion"] in {"louvain", "kmeans", "agglomerative", "dbscan"}
    assert set(metrics["ablation"]) == {"structure_only", "structure_cochange", "all_signals"}
    assert modules["result"]["projection"]

    ranking = tasks["change_impact"]["result"]["ranking_evaluation"]
    assert ranking["status"] == "evaluated"
    assert {"static_dependency", "co_change", "weighted"} <= set(ranking["approaches"])

    impact = client.get(
        f"/api/v1/repositories/{repository_id}/impact",
        params={"path": "src/billing/tax.ts"},
        headers=OWNER,
    ).json()
    assert impact["impacted"]
    weights = {"dependency": 0.35, "co_change": 0.30, "proximity": 0.20, "bug_correlation": 0.15}
    for item in impact["impacted"]:
        signals = item["signals"]
        assert set(signals) == set(weights)
        assert all(0.0 <= value <= 1.0 for value in signals.values())
        expected = round(sum(weights[name] * signals[name] for name in weights), 4)
        assert abs(item["weighted_score"] - expected) < 1e-3
    # No import graph was seeded, so the support here is co-change history.
    assert any(item["signals"]["co_change"] > 0 for item in impact["impacted"])
    assert any("intent model classified" in line for line in impact["limitations"])
