"""Multi-signal module discovery, richer defect features, and weighted impact ranking."""

import random
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

from code_genome_ml import (
    ChangeRecord,
    CommitRecord,
    FileRecord,
    ImpactSignalContext,
    ImportRecord,
    TrainingInputs,
    rank_weighted_impact,
    train_all,
    weighted_impact_score,
)
from code_genome_ml.defect import train_defect_model
from code_genome_ml.impact_ranking import evaluate_impact_ranking, ranking_scores
from code_genome_ml.modules import ALGORITHMS, discover_modules, select_champion
from test_models import synthetic_repository

FEATURES = ["billing", "auth", "search", "ui"]
LAYERS = {"models": "ts", "views": "tsx", "controllers": "ts"}
START = datetime(2026, 1, 1, 9, tzinfo=UTC)


def layered_repository(commits: int = 160, seed: int = 3) -> TrainingInputs:
    """Directories are layers, but files change, and are written, by feature.

    No import graph is supplied, so structure alone cannot see the hidden feature modules."""
    rng = random.Random(seed)
    files = [
        FileRecord(
            f"src/{layer}/{feature}.{extension}",
            900,
            (f"{feature}{layer.title()}", f"load{feature.title()}"),
        )
        for layer, extension in LAYERS.items()
        for feature in FEATURES
    ]
    authors = {"billing": "asha", "auth": "ravi", "search": "li", "ui": "sam"}
    records, changes = [], []
    for index in range(commits):
        feature = rng.choice(FEATURES)
        when = START + timedelta(days=index, hours=rng.randint(0, 6))
        layers = rng.sample(list(LAYERS), rng.randint(2, 3))
        sha = f"{index:040x}"
        author = authors[feature] if rng.random() < 0.85 else rng.choice(list(authors.values()))
        records.append(CommitRecord(sha, f"feat: update {feature}", author, when))
        for layer in layers:
            changes.append(ChangeRecord(sha, f"src/{layer}/{feature}.{LAYERS[layer]}", when, 10))
    return TrainingInputs(records, changes, files, [])


def _discover(inputs: TrainingInputs):  # type: ignore[no-untyped-def]
    return discover_modules(
        sorted(item.path for item in inputs.files),
        inputs.changes,
        inputs.imports,
        {item.path: item.symbols for item in inputs.files},
        {commit.sha: commit.author for commit in inputs.commits},
    )


def test_module_discovery_compares_algorithms_and_keeps_old_keys() -> None:
    result = _discover(synthetic_repository())
    assert result.status == "trained"
    metrics = result.metrics
    # Backward-compatible keys still describe the selected clustering.
    for key in (
        "communities",
        "clustered_files",
        "isolated_files",
        "modularity_learned",
        "modularity_directory_baseline",
        "directory_groups",
    ):
        assert key in metrics
    assert set(metrics["algorithms"]) == set(ALGORITHMS)
    assert metrics["champion"] in ALGORITHMS
    for scores in metrics["algorithms"].values():
        assert {"silhouette", "davies_bouldin", "modularity", "heldout_lift"} <= set(
            cast("Iterable[str]", scores)
        )
    assert metrics["algorithms"]["kmeans"]["parameters"]["k"] >= 2
    assert "eps" in metrics["algorithms"]["dbscan"]["parameters"]
    assert set(metrics["ablation"]) == {
        "structure_only",
        "structure_cochange",
        "all_signals",
    }
    assert metrics["modularity_learned"] > 0.3
    assert len(result.modules) >= 4
    assert result.projection_method == "pca"
    assert len(result.projection) == metrics["clustered_files"]
    clusters = {point["cluster"] for point in result.projection}
    assert clusters <= set(range(len(result.modules))) | {-1}
    assert all(set(point) == {"path", "x", "y", "cluster"} for point in result.projection)


def test_history_signals_find_feature_modules_that_directories_and_structure_miss() -> None:
    result = _discover(layered_repository())
    assert result.status == "trained"
    metrics = result.metrics
    champion = metrics["algorithms"][metrics["champion"]]
    directory = metrics["directory_baseline"]
    # Directories group by layer; the learned modules follow features and keep later
    # co-changes together far more often.
    assert champion["heldout_lift"] > directory["heldout_lift"]
    assert metrics["beats_directory_baseline"]["heldout_lift"] is True
    features = {
        frozenset(path.rsplit("/", 1)[1].split(".")[0] for path in module.files)
        for module in result.modules
    }
    assert sum(1 for names in features if len(names) == 1) >= 3
    ablation = metrics["ablation"]
    # Without imports, structure alone cannot separate anything.
    assert ablation["structure_only"]["clusters"] <= 1
    assert ablation["all_signals"]["heldout_lift"] > ablation["structure_only"]["heldout_lift"]


def test_champion_rule_excludes_degenerate_clusterings() -> None:
    scores: dict[str, dict[str, object]] = {
        "dbscan": {
            "clusters": 4,
            "unassigned_share": 0.6,
            "silhouette": 0.9,
            "davies_bouldin": 0.1,
            "modularity": 0.9,
            "heldout_lift": 9.0,
        },
        "kmeans": {
            "clusters": 3,
            "unassigned_share": 0.0,
            "silhouette": 0.3,
            "davies_bouldin": 1.0,
            "modularity": 0.4,
            "heldout_lift": 2.0,
        },
        "louvain": {
            "clusters": 3,
            "unassigned_share": 0.0,
            "silhouette": 0.3,
            "davies_bouldin": 1.0,
            "modularity": 0.4,
            "heldout_lift": 2.0,
        },
    }
    champion, ranks = select_champion(scores)
    assert "dbscan" not in ranks
    assert champion == "louvain"  # tie goes to the more established method
    assert select_champion({"kmeans": {"clusters": 1}}) == (None, {})


def test_module_discovery_abstains_on_sparse_graph() -> None:
    result = discover_modules(["a.ts", "b.ts"], [], [])
    assert result.status == "insufficient_data"
    assert result.projection == []


def test_defect_model_adds_graph_ownership_and_code_metric_features() -> None:
    inputs = synthetic_repository()
    fix_shas = {
        commit.sha
        for commit in inputs.commits
        if any(word in commit.message for word in ("fix", "resolve", "handle"))
    }
    without = train_defect_model(
        inputs.commits, inputs.changes, fix_shas, inputs.files, inputs.imports
    )
    assert without.status == "trained"
    assert without.model_version == "defect-temporal@3"
    features = without.dataset["features"]
    assert {"betweenness", "pagerank", "ownership"} <= set(cast("Iterable[str]", features))
    assert not {"log_loc", "log_complexity", "log_functions"} & set(cast("Iterable[str]", features))
    assert without.dataset["code_metrics"]["missing"] == ["loc", "complexity", "functions"]  # type: ignore[index]
    for name in ("logistic_regression", "random_forest", "gradient_boosting", "heuristic_baseline"):
        scores = without.metrics[name]
        assert {"precision", "recall", "f1", "roc_auc", "average_precision"} <= set(
            cast("Iterable[str]", scores)
        )
    assert "decision_rule" in without.metrics
    ownership = without.predictions[0].features["ownership"]
    assert 0 < ownership <= 1

    # The analyzer recorded loc and complexity on most files (not functions, not all files).
    measured = [
        replace(item, loc=40 + index * 10, complexity=float(index % 5 + 1)) if index % 4 else item
        for index, item in enumerate(inputs.files)
    ]
    enriched = train_defect_model(
        inputs.commits, inputs.changes, fix_shas, measured, inputs.imports
    )
    assert enriched.status == "trained"
    assert {"log_loc", "log_complexity"} <= set(cast("Iterable[str]", enriched.dataset["features"]))
    assert "log_functions" not in enriched.dataset["features"]  # type: ignore[operator]
    code = enriched.dataset["code_metrics"]
    assert code["used"] == ["loc", "complexity"]  # type: ignore[index]
    assert code["missing"] == ["functions"]  # type: ignore[index]
    assert code["files_measured"]["loc"] == 15  # type: ignore[index]


def _toy_history() -> tuple[list[ChangeRecord], list[ImportRecord]]:
    times = [START + timedelta(days=day) for day in range(4)]
    changes = [
        ChangeRecord("c1", "a.ts", times[0], 5),
        ChangeRecord("c1", "b.ts", times[0], 5),
        ChangeRecord("c2", "a.ts", times[1], 5),
        ChangeRecord("c2", "b.ts", times[1], 5),
        ChangeRecord("c3", "a.ts", times[2], 5),
        ChangeRecord("c4", "a.ts", times[3], 5),
        ChangeRecord("c4", "c.ts", times[3], 5),
    ]
    imports = [ImportRecord("a.ts", "b.ts"), ImportRecord("b.ts", "c.ts")]
    return changes, imports


def test_weighted_impact_components_follow_the_spec() -> None:
    changes, imports = _toy_history()
    context = ImpactSignalContext(changes, imports, {"c4"})
    assert context.signals("a.ts", "b.ts") == {
        "dependency": 1.0,
        "co_change": 0.5,
        "proximity": 1.0,
        "bug_correlation": 0.0,
    }
    far = context.signals("a.ts", "c.ts")
    assert far == {
        "dependency": 0.5,
        "co_change": 0.25,
        "proximity": 0.6667,
        "bug_correlation": 1.0,
    }
    assert weighted_impact_score(context.signals("a.ts", "b.ts")) == 0.7
    assert weighted_impact_score(far) == round(0.35 * 0.5 + 0.3 * 0.25 + 0.2 * 0.6667 + 0.15, 4)
    # Reverse direction: c is imported transitively by a; c changed once, always with a.
    assert context.signals("c.ts", "a.ts")["dependency"] == 0.5
    assert context.signals("c.ts", "a.ts")["co_change"] == 1.0
    ranked = rank_weighted_impact(context, "a.ts")
    assert [item.path for item in ranked] == ["b.ts", "c.ts"]
    assert context.signals("a.ts", "unknown.ts") == {
        "dependency": 0.0,
        "co_change": 0.0,
        "proximity": 0.0,
        "bug_correlation": 0.0,
    }
    assert weighted_impact_score({"dependency": 3.0, "co_change": -1.0}) == 0.35


def test_ranking_metrics_at_k() -> None:
    precision, recall, average = ranking_scores(["x", "a", "y", "b"], {"a", "b", "c"}, 5)
    assert precision == 0.4
    assert recall == 2 / 3
    assert round(average, 4) == round((1 / 2 + 2 / 4) / 3, 4)
    assert ranking_scores([], {"a"}, 5) == (0.0, 0.0, 0.0)


def test_impact_ranking_evaluation_compares_four_approaches() -> None:
    inputs = synthetic_repository()
    fix_shas = {commit.sha for commit in inputs.commits if "fix" in commit.message}
    result = evaluate_impact_ranking(inputs.changes, inputs.imports, fix_shas, len(inputs.files))
    assert result["status"] == "evaluated"
    approaches = result["approaches"]
    assert set(cast("Iterable[str]", approaches)) == {
        "static_dependency",
        "co_change",
        "weighted",
        "link_model",
        "learned_weights",
    }
    for scores in approaches.values():  # type: ignore[attr-defined]
        assert set(cast("Iterable[str]", scores)) == {
            f"{metric}_at_{k}" for metric in ("precision", "recall", "map") for k in (5, 10)
        }
        assert all(0.0 <= value <= 1.0 for value in scores.values())
    assert result["queries"] >= 10  # type: ignore[operator]
    assert approaches["weighted"]["map_at_10"] >= approaches["static_dependency"]["map_at_10"]  # type: ignore[index]
    assert set(cast("Iterable[str]", result["learned_component_weights"])) == {
        "dependency",
        "co_change",
        "proximity",
        "bug_correlation",
    }

    short = synthetic_repository(commits=12)
    abstained = evaluate_impact_ranking(short.changes, short.imports, set(), len(short.files))
    assert abstained["status"] == "insufficient_data"
    assert abstained["reason"]


def test_pipeline_reports_ranking_and_weighted_score_definitions() -> None:
    results = train_all(synthetic_repository())
    impact = results["change_impact"]
    assert impact["ranking_evaluation"]["status"] == "evaluated"
    assert impact["weighted_score"]["weights"] == {
        "dependency": 0.35,
        "co_change": 0.30,
        "proximity": 0.20,
        "bug_correlation": 0.15,
    }
    modules = results["modules"]
    assert modules["model_version"] == "modules-multisignal@2"
    assert modules["projection"]
