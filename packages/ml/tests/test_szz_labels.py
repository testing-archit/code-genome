from datetime import UTC, datetime, timedelta

from code_genome_ml import BugLinkRecord
from code_genome_ml.defect import SZZ_LABEL_VERSION, _szz_labels, train_defect_model
from test_models import synthetic_repository


def test_szz_labels_never_use_a_fix_from_after_the_cutoff() -> None:
    day = datetime(2026, 1, 1, tzinfo=UTC)
    times = {f"c{index}": day + timedelta(days=index) for index in range(10)}
    links = [
        BugLinkRecord("a.ts", "c2", "c4", 0.7),  # introduced and fixed before the cut-off
        BugLinkRecord("b.ts", "c3", "c8", 0.7),  # fixed only after the cut-off
        BugLinkRecord("c.ts", "c2", "c4", 0.35),  # bulk commit: below the confidence floor
        BugLinkRecord("d.ts", "c7", "c9", 0.7),  # introduced after the window
    ]
    paths = ["a.ts", "b.ts", "c.ts", "d.ts"]
    start, end = times["c1"], times["c5"]
    assert _szz_labels(paths, links, times, start, end, fixed_before=end).tolist() == [1, 0, 0, 0]
    # Without the cut-off (evaluation targets), later fixes reveal the bug.
    assert _szz_labels(paths, links, times, start, end, fixed_before=None).tolist() == [1, 1, 0, 0]
    assert _szz_labels(paths, links, times, end, None, fixed_before=None).tolist() == [0, 0, 0, 1]


def _links_for(inputs: object) -> list[BugLinkRecord]:
    """Blame each fix on the previous commit that touched the same file (a toy SZZ)."""
    commits = sorted(inputs.commits, key=lambda item: item.authored_at)  # type: ignore[attr-defined]
    touched: dict[str, set[str]] = {}
    for change in inputs.changes:  # type: ignore[attr-defined]
        touched.setdefault(change.commit_sha, set()).add(change.path)
    last: dict[str, str] = {}
    links: list[BugLinkRecord] = []
    for commit in commits:
        for path in sorted(touched.get(commit.sha, ())):
            if commit.message.split(": ")[-1].startswith(("fix ", "resolve ", "handle ")):
                if path in last:
                    links.append(BugLinkRecord(path, last[path], commit.sha, 0.7))
            else:
                last[path] = commit.sha
    return links


def test_szz_labels_are_reported_beside_the_deployed_fix_touch_model() -> None:
    inputs = synthetic_repository()
    fix_shas = {
        commit.sha
        for commit in inputs.commits
        if commit.message.split(": ")[-1].startswith(("fix ", "resolve ", "handle "))
    }
    args = (inputs.commits, inputs.changes, fix_shas, inputs.files, inputs.imports)
    baseline = train_defect_model(*args)
    links = _links_for(inputs)
    assert links
    compared = train_defect_model(*args, links)
    report = compared.metrics["szz_labels"]
    assert isinstance(report, dict)
    assert report["label_version"] == SZZ_LABEL_VERSION
    assert report["links_used"] == len(links)
    assert report["status"] == "evaluated"
    assert 0 <= report["random_forest"]["roc_auc"] <= 1
    assert "heuristic_baseline" in report
    # The deployed model and its predictions do not change with the comparison.
    assert compared.champion == baseline.champion
    assert [item.path for item in compared.predictions] == [
        item.path for item in baseline.predictions
    ]
    assert "szz_labels" not in baseline.metrics
    assert str(compared.dataset["label_source"]).startswith("fix-touch")
