from datetime import UTC, datetime, timedelta

from code_genome_api.services.ownership import History, bus_factor, is_bot, suggest_reviewers

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _history(entries: list[tuple[str, str, int, set[str]]]) -> History:
    """(sha, author email, days ago, files)."""
    return History(
        authors={sha: (email.split("@")[0].title(), email) for sha, email, _, _ in entries},
        when={sha: NOW - timedelta(days=days) for sha, _, days, _ in entries},
        files_by_commit={sha: files for sha, _, _, files in entries},
        newest=NOW,
    )


def test_recent_contributors_outrank_former_ones_and_only_relevant_files_count() -> None:
    history = _history(
        [
            *[(f"old{i}", "past@x.dev", 700, {"src/pay.ts"}) for i in range(4)],
            ("new1", "current@x.dev", 5, {"src/pay.ts"}),
            ("new2", "current@x.dev", 30, {"src/pay.ts", "src/tax.ts"}),
            ("other", "unrelated@x.dev", 1, {"docs/readme.md"}),
        ]
    )
    reviewers = suggest_reviewers(history, ["src/pay.ts", "src/tax.ts"])
    assert [item.email for item in reviewers] == ["current@x.dev", "past@x.dev"]
    assert reviewers[0].files == {"src/pay.ts", "src/tax.ts"}
    assert reviewers[0].evidence_shas == ["new1", "new2"]
    assert suggest_reviewers(history, []) == []


def test_bus_factor_counts_people_covering_half_the_commits() -> None:
    concentrated = _history(
        [(f"a{i}", "owner@x.dev", i, {"src/core/a.ts"}) for i in range(9)]
        + [
            ("b0", "helper@x.dev", 1, {"src/core/b.ts"}),
            ("b1", "helper2@x.dev", 2, {"src/core/b.ts"}),
        ]
    )
    factor, top = bus_factor(concentrated, ["src/core/a.ts", "src/core/b.ts"]) or (0, [])
    assert factor == 1 and top[0] == ("Owner", 9)
    shared = _history([(f"s{i}", f"dev{i % 4}@x.dev", i, {"lib/x.ts"}) for i in range(12)])
    assert (bus_factor(shared, ["lib/x.ts"]) or (0, []))[0] == 2
    quiet = _history([("q", "dev@x.dev", 1, {"lib/y.ts"})])
    assert bus_factor(quiet, ["lib/y.ts"]) is None


def test_bots_are_recognised() -> None:
    assert is_bot("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
    assert is_bot("Renovate Bot", "bot@renovateapp.com")
    assert not is_bot("Sindre Sorhus", "sindresorhus@gmail.com")
