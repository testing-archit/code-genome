from datetime import UTC, datetime

from code_genome_evolution import CommitChange, analyze_evolution


def test_seeded_cochange_and_inferred_module_documentation_are_stable() -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    commits = (
        CommitChange("a" * 40, now, ("src/a.ts", "src/b.ts"), 20),
        CommitChange("b" * 40, now, ("src/a.ts", "src/b.ts", "tests/a.ts"), 30),
        CommitChange("c" * 40, now, ("src/a.ts",), 5),
    )
    result = analyze_evolution(commits, ("src/a.ts", "src/b.ts", "tests/a.ts"))

    strongest = result.co_changes[0]
    assert (strongest.left_path, strongest.right_path) == ("src/a.ts", "src/b.ts")
    assert strongest.commit_count == 2
    assert strongest.confidence == 0.6667
    assert result.hotspots[0].path == "src/a.ts"
    assert all(module.inferred for module in result.modules)
    assert all(module.evidence_shas for module in result.modules)
    assert all(module.description.startswith("Inferred") for module in result.modules)
