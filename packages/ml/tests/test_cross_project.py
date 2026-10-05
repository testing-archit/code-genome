from code_genome_ml import keyword_fix_shas
from code_genome_ml.cross_project import (
    CROSS_VERSION,
    RepoDataset,
    evaluate_cross_project,
    repository_dataset,
)
from test_models import synthetic_repository


def _dataset(seed: int, commits: int = 180) -> RepoDataset | str:
    inputs = synthetic_repository(seed=seed, commits=commits)
    return repository_dataset(
        f"repo{seed}",
        inputs.commits,
        inputs.changes,
        keyword_fix_shas(inputs.commits),
        inputs.files,
        inputs.imports,
    )


def test_leave_one_repository_out_compares_cross_within_and_heuristic() -> None:
    datasets = [
        item for item in (_dataset(seed) for seed in (1, 2, 3, 4)) if isinstance(item, RepoDataset)
    ]
    assert len(datasets) >= 3
    report = evaluate_cross_project(datasets)
    assert report["model_version"] == CROSS_VERSION and report["status"] == "evaluated"
    per_repository = report["per_repository"]
    assert isinstance(per_repository, dict) and set(per_repository) == {
        item.name for item in datasets
    }
    for row in per_repository.values():
        for approach in ("heuristic", "within_logistic_regression", "cross_random_forest"):
            assert 0 <= row[approach]["average_precision"] <= 1
    mean = report["mean"]
    assert isinstance(mean, dict) and report["best_mean_average_precision"] in mean
    # Synthetic repositories share the same bug-prone files, so pooled history should help.
    assert mean["cross_logistic_regression"]["roc_auc"] >= 0.7


def test_small_histories_and_too_few_repositories_are_reported() -> None:
    assert isinstance(_dataset(1, commits=10), str)
    usable = [item for item in (_dataset(seed) for seed in (1, 2)) if isinstance(item, RepoDataset)]
    report = evaluate_cross_project(usable)
    assert report["status"] == "insufficient_data"
