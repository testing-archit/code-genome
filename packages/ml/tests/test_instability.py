import random
from datetime import UTC, datetime, timedelta

from code_genome_ml import group_components
from code_genome_ml.defect import train_defect_model
from code_genome_ml.instability import MODEL_VERSION, train_instability_model
from code_genome_ml.records import ChangeRecord, CommitRecord, FileRecord
from test_models import synthetic_repository

AREAS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]


def sequential_history(
    weeks: int = 60, seed: int = 3
) -> tuple[list[CommitRecord], list[ChangeRecord], set[str], list[FileRecord]]:
    """Weekly history whose bug fixes depend on the *sequence* of earlier weeks.

    * alpha, beta: a bug-fix week is followed by a quiet week, then fixes again
      (alternating), so "next week repeats this week" is systematically wrong.
    * gamma, delta: a burst of feature commits is followed by a fix the next week.
    * epsilon, zeta: steady feature work, never fixed.
    """
    rng = random.Random(seed)
    start = datetime(2025, 1, 6, 9, tzinfo=UTC)
    files = [FileRecord(f"src/{area}/{name}.ts", 2000) for area in AREAS for name in "abc"]
    commits: list[CommitRecord] = []
    changes: list[ChangeRecord] = []
    fixes: set[str] = set()
    burst_last_week = {"gamma": False, "delta": False}

    def commit(area: str, week: int, is_fix: bool) -> None:
        index = len(commits)
        when = start + timedelta(weeks=week, hours=rng.randint(0, 100))
        sha = f"{index:040x}"
        commits.append(
            CommitRecord(sha, "fix bug" if is_fix else "add feature", rng.choice("abcd"), when)
        )
        if is_fix:
            fixes.add(sha)
        for name in rng.sample("abc", rng.randint(1, 2)):
            changes.append(ChangeRecord(sha, f"src/{area}/{name}.ts", when, rng.randint(3, 40)))

    for week in range(weeks):
        for offset, area in enumerate(("alpha", "beta")):
            if (week + offset) % 2 == 0:
                commit(area, week, is_fix=True)
        for area in ("gamma", "delta"):
            if burst_last_week[area] and rng.random() < 0.9:
                commit(area, week, is_fix=True)
            burst = rng.random() < 0.3
            for _ in range(4 if burst else rng.randint(0, 1)):
                commit(area, week, is_fix=False)
            burst_last_week[area] = burst
        for area in ("epsilon", "zeta"):
            for _ in range(rng.randint(1, 3)):
                commit(area, week, is_fix=False)
    return commits, changes, fixes, files


def test_instability_model_beats_persistence_on_held_out_periods() -> None:
    commits, changes, fixes, files = sequential_history()
    result = train_instability_model(commits, changes, fixes, files)
    assert result.status == "trained", result.reason
    assert result.model_version == MODEL_VERSION
    assert "windowed" in MODEL_VERSION and "gru" not in MODEL_VERSION.lower()
    assert result.dataset["period_kind"] == "week"
    assert result.dataset["test_samples"] and result.dataset["train_samples"]

    model = result.metrics["model"]
    persistence = result.metrics["baseline_persistence"]
    assert isinstance(model, dict) and isinstance(persistence, dict)
    assert model["roc_auc"] >= 0.85
    assert model["average_precision"] > persistence["average_precision"] + 0.2
    assert model["f1"] > persistence["f1"]
    assert "baseline_historical_rate" in result.metrics

    forecasts = {item.component: item for item in result.predictions}
    assert set(forecasts) == {f"src/{area}" for area in AREAS}
    for area in ("epsilon", "zeta"):
        assert forecasts[f"src/{area}"].probability < 0.2
    # alpha and beta alternate, so exactly one of them fixed last week; the model should
    # expect the *other* one next.
    fixed_last = [area for area in ("alpha", "beta") if forecasts[f"src/{area}"].fixed_last_period]
    assert len(fixed_last) == 1
    other = "beta" if fixed_last[0] == "alpha" else "alpha"
    assert forecasts[f"src/{other}"].probability > forecasts[f"src/{fixed_last[0]}"].probability
    top = result.predictions[0]
    assert top.contributions and top.evidence_shas
    assert len(top.recent_periods) == result.dataset["window"]
    assert all(name in result.feature_labels for name, _ in top.contributions)


def test_instability_model_abstains_on_short_history() -> None:
    commits, changes, fixes, files = sequential_history(weeks=3)
    result = train_instability_model(commits, changes, fixes, files)
    assert result.status == "insufficient_data"
    assert result.reason and result.predictions == []


def test_instability_model_abstains_without_fix_signal() -> None:
    commits, changes, _, files = sequential_history()
    result = train_instability_model(commits, changes, set(), files)
    assert result.status == "insufficient_data"
    assert "bug-fix" in (result.reason or "")


def test_sparse_history_uses_commit_windows() -> None:
    commits, changes, fixes, files = sequential_history()
    # Spread the same commits over ten years so weekly periods would be mostly empty.
    stretched = [
        CommitRecord(c.sha, c.message, c.author, c.authored_at + timedelta(days=index * 9))
        for index, c in enumerate(commits)
    ]
    when = {c.sha: c.authored_at for c in stretched}
    moved = [ChangeRecord(c.commit_sha, c.path, when[c.commit_sha], c.churn) for c in changes]
    result = train_instability_model(stretched, moved, fixes, files)
    assert result.dataset["period_kind"] == "commit_window"


def test_component_grouping_matches_directory_depth() -> None:
    mapping = group_components([f"src/{area}/{name}.ts" for area in AREAS for name in "abc"])
    assert set(mapping.values()) == {f"src/{area}" for area in AREAS}


def test_defect_model_compares_random_forest_and_explains_the_champion() -> None:
    inputs = synthetic_repository()
    fix_shas = {
        commit.sha
        for commit in inputs.commits
        if any(word in commit.message for word in ("fix", "resolve", "handle"))
    }
    result = train_defect_model(
        inputs.commits, inputs.changes, fix_shas, inputs.files, inputs.imports
    )
    assert result.model_version == "defect-temporal@3"
    assert {"logistic_regression", "random_forest", "gradient_boosting"} <= set(result.metrics)
    forest = result.metrics["random_forest"]
    assert isinstance(forest, dict) and forest["roc_auc"] > 0.5
    assert result.contribution_method and result.contribution_unit
    if result.champion == "logistic_regression":
        assert result.contribution_unit == "log-odds"
    else:
        assert result.contribution_unit == "probability"
        assert all(abs(value) <= 1 for _, value in result.predictions[0].contributions)
