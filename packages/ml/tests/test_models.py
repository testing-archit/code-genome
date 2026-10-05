import random
from datetime import UTC, datetime, timedelta
from typing import cast

from code_genome_ml import (
    ChangeRecord,
    CommitRecord,
    FileRecord,
    HybridRetriever,
    ImportRecord,
    SearchDocument,
    TrainingInputs,
    file_document,
    predict_impact,
    tokenize,
    train_all,
)
from code_genome_ml.defect import train_defect_model
from code_genome_ml.intent import train_intent_classifier

COMPONENTS = {
    "src/billing": ["invoice.ts", "export.ts", "tax.ts", "currency.ts"],
    "src/auth": ["login.ts", "session.ts", "token.ts", "password.ts"],
    "src/ui": ["button.tsx", "table.tsx", "modal.tsx", "form.tsx"],
    "src/api": ["client.ts", "routes.ts", "errors.ts", "retry.ts"],
    "src/search": ["indexer.ts", "query.ts", "ranker.ts", "cache.ts"],
}
BUGGY = {"src/billing/tax.ts", "src/billing/currency.ts", "src/auth/session.ts", "src/api/retry.ts"}
MESSAGES = {
    "feat": ["add {name} support", "implement {name} flow", "introduce {name} endpoint"],
    "fix": ["fix {name} crash", "resolve {name} null error", "handle missing {name} value"],
    "refactor": ["refactor {name} module", "extract {name} helpers"],
    "test": ["add tests for {name}", "cover {name} edge cases"],
    "docs": ["document {name} usage"],
}


def synthetic_repository(seed: int = 1, commits: int = 180) -> TrainingInputs:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, 9, tzinfo=UTC)
    files = [
        FileRecord(
            f"{directory}/{name}",
            1200 + index * 300,
            (name.split(".")[0], f"{name.split('.')[0]}Handler"),
            None,
        )
        for directory, names in COMPONENTS.items()
        for index, name in enumerate(names)
    ]
    imports = []
    for directory, names in COMPONENTS.items():
        for left, right in zip(names, names[1:], strict=False):
            imports.append(ImportRecord(f"{directory}/{left}", f"{directory}/{right}"))
        imports.append(ImportRecord(f"{directory}/{names[0]}", "src/api/client.ts"))
    records: list[CommitRecord] = []
    changes: list[ChangeRecord] = []
    for index in range(commits):
        when = start + timedelta(days=index * 1.7, hours=rng.randint(0, 8))
        directory = rng.choice(list(COMPONENTS))
        names = COMPONENTS[directory]
        touched = rng.sample(names, rng.randint(1, 3))
        paths = [f"{directory}/{name}" for name in touched]
        is_fix = any(path in BUGGY for path in paths) and rng.random() < 0.7
        if is_fix:
            paths = [path for path in paths if path in BUGGY]
            touched = [path.rsplit("/", 1)[1] for path in paths]
        kind = "fix" if is_fix else rng.choice(["feat", "refactor", "test", "docs", "feat"])
        subject = rng.choice(MESSAGES[kind]).format(name=touched[0].split(".")[0])
        prefix = f"{kind}: " if rng.random() < 0.5 else ""
        sha = f"{index:040x}"
        records.append(
            CommitRecord(sha, prefix + subject, rng.choice(["asha", "ravi", "li", "sam"]), when)
        )
        for path in paths:
            changes.append(ChangeRecord(sha, path, when, rng.randint(2, 60)))
    # One sweeping, unusual commit to exercise the anomaly detector.
    when = start + timedelta(days=commits * 1.7 + 1, hours=3)
    records.append(CommitRecord("f" * 40, "sweep", "bot", when))
    for item in files:
        changes.append(ChangeRecord("f" * 40, item.path, when, 900))
    return TrainingInputs(records, changes, files, imports)


def test_tokenizer_splits_identifiers_and_translates_hinglish() -> None:
    assert tokenize("parseInvoiceCSV export_rows") == ["pars", "invoic", "csv", "export", "row"]
    # Question words and Hindi fillers are dropped; content words map to English stems.
    assert tokenize("billing kahan hai") == tokenize("बिलिंग कहाँ है") == ["bill"]
    assert tokenize("हाल में क्या बदला") == tokenize("recently changed") == ["recent", "chang"]
    assert tokenize("changes") == tokenize("change") == tokenize("changed")


def test_intent_classifier_beats_majority_baseline() -> None:
    inputs = synthetic_repository()
    result = train_intent_classifier(inputs.commits)
    assert result.status == "trained"
    assert result.metrics["cv_macro_f1"] > result.metrics["majority_baseline_macro_f1"] + 0.3  # type: ignore[operator]
    assert result.dataset["weak_labels"] > 30  # type: ignore[operator]
    predicted = {item.sha: item for item in result.predictions}
    unlabelled_fixes = [
        commit
        for commit in inputs.commits
        if not commit.message.startswith("fix:")
        and commit.message.startswith(("fix ", "resolve ", "handle "))
    ]
    assert unlabelled_fixes
    accuracy = sum(predicted[commit.sha].intent == "fix" for commit in unlabelled_fixes) / len(
        unlabelled_fixes
    )
    assert accuracy >= 0.8
    assert "fix" in result.top_terms


def test_defect_model_learns_from_history_and_explains_predictions() -> None:
    inputs = synthetic_repository()
    fix_shas = {
        commit.sha
        for commit in inputs.commits
        if "fix" in commit.message or "resolve" in commit.message or "handle" in commit.message
    }
    result = train_defect_model(
        inputs.commits, inputs.changes, fix_shas, inputs.files, inputs.imports
    )
    assert result.status == "trained"
    assert result.champion in {"logistic_regression", "random_forest", "gradient_boosting"}
    champion = cast("dict[str, float]", result.metrics[str(result.champion)])
    assert champion["roc_auc"] >= 0.75
    top = {item.path for item in result.predictions[:4]}
    assert len(top & BUGGY) >= 3
    assert result.predictions[0].contributions
    assert result.calibration["predicted"]
    assert result.importance


def test_defect_model_abstains_without_enough_history() -> None:
    inputs = synthetic_repository(commits=10)
    result = train_defect_model(inputs.commits, inputs.changes, set(), inputs.files, inputs.imports)
    assert result.status == "insufficient_data"
    assert result.predictions == []


def test_full_pipeline_trains_every_task_with_evaluations() -> None:
    inputs = synthetic_repository()
    results = train_all(inputs)
    assert set(results) == {
        "commit_intent",
        "defect_risk",
        "change_impact",
        "retrieval",
        "modules",
        "anomalies",
        "instability",
    }
    assert all(result["status"] == "trained" for result in results.values()), {
        name: result.get("reason") for name, result in results.items()
    }

    impact = results["change_impact"]
    assert impact["metrics"]["model"]["roc_auc"] >= 0.85
    assert {"baseline_past_cochange", "baseline_import_distance"} <= set(impact["metrics"])
    predictions = predict_impact(
        impact, "src/billing/tax.ts", inputs.changes, inputs.imports, [f.path for f in inputs.files]
    )
    assert predictions and all(item.path.startswith("src/billing/") for item in predictions[:3])

    retrieval = results["retrieval"]["metrics"]
    assert retrieval["hybrid"]["recall_at_10"] > retrieval["random_baseline_recall"]

    modules = results["modules"]
    assert modules["metrics"]["modularity_learned"] > 0.3
    assert len(modules["modules"]) >= 4

    anomalies = results["anomalies"]["anomalies"]
    assert anomalies[0]["sha"] == "f" * 40
    assert any("files touched" in reason for reason in anomalies[0]["reasons"])


def test_hybrid_search_matches_hinglish_and_identifiers() -> None:
    inputs = synthetic_repository()
    documents = [file_document(item) for item in inputs.files] + [
        SearchDocument(
            "module:billing", "module", "Billing owns invoice export and tax.", "billing"
        )
    ]
    retriever = HybridRetriever(documents)
    hits = retriever.search("invoice kahan hai", limit=3)
    assert hits and hits[0].document.path in {"src/billing/invoice.ts", None}
    assert retriever.search("sessionHandler", limit=1)[0].document.path == "src/auth/session.ts"
    assert retriever.search("quantum chromodynamics") == []


def test_retrieval_mode_is_selected_by_evaluation() -> None:
    from code_genome_ml.retrieval import select_mode

    keyword_wins = {
        "bm25": {"recall_at_10": 0.50, "mrr": 0.47},
        "semantic": {"recall_at_10": 0.43, "mrr": 0.4},
        "hybrid": {"recall_at_10": 0.47, "mrr": 0.43},
    }
    assert select_mode(keyword_wins) == "bm25"
    near_tie = {
        "bm25": {"recall_at_10": 0.475, "mrr": 0.5},
        "semantic": {"recall_at_10": 0.3, "mrr": 0.3},
        "hybrid": {"recall_at_10": 0.47, "mrr": 0.43},
    }
    assert select_mode(near_tie) == "hybrid"
