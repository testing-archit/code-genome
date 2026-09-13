import itertools
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .types import CoChange, CommitChange, EvolutionResult, Hotspot, ModuleCandidate


def _module_key(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "repository-root"


def analyze_evolution(
    commits: tuple[CommitChange, ...], current_files: tuple[str, ...]
) -> EvolutionResult:
    file_commits: dict[str, list[str]] = defaultdict(list)
    file_churn: Counter[str] = Counter()
    pair_commits: dict[tuple[str, str], list[str]] = defaultdict(list)
    for commit in commits:
        files = tuple(sorted(set(commit.files)))
        per_file_churn = max(1, commit.churn // max(1, len(files)))
        for path in files:
            file_commits[path].append(commit.sha)
            file_churn[path] += per_file_churn
        for pair in itertools.combinations(files, 2):
            pair_commits[pair].append(commit.sha)

    co_changes = []
    for (left, right), shas in pair_commits.items():
        if len(shas) < 2:
            continue
        union = len(set(file_commits[left]) | set(file_commits[right]))
        co_changes.append(
            CoChange(left, right, len(shas), round(len(shas) / union, 4), tuple(shas[:20]))
        )
    co_changes.sort(key=lambda item: (-item.commit_count, item.left_path, item.right_path))

    max_commits = max((len(shas) for shas in file_commits.values()), default=1)
    max_churn = max(file_churn.values(), default=1)
    hotspots = [
        Hotspot(
            path=path,
            commit_count=len(shas),
            churn=file_churn[path],
            score=round(0.7 * len(shas) / max_commits + 0.3 * file_churn[path] / max_churn, 4),
            evidence_shas=tuple(shas[:20]),
        )
        for path, shas in file_commits.items()
    ]
    hotspots.sort(key=lambda item: (-item.score, item.path))

    grouped: dict[str, list[str]] = defaultdict(list)
    for path in sorted(current_files):
        grouped[_module_key(path)].append(path)
    modules = []
    for key, module_files in sorted(grouped.items()):
        evidence = tuple(
            dict.fromkeys(sha for path in module_files for sha in file_commits.get(path, []))
        )[:20]
        changed_files = sum(path in file_commits for path in module_files)
        confidence = round(0.5 + 0.5 * changed_files / max(1, len(module_files)), 4)
        description = (
            f"Inferred directory module containing {len(module_files)} current files; "
            f"{changed_files} have observed commit evidence."
        )
        modules.append(
            ModuleCandidate(
                key, tuple(module_files), confidence, description, evidence, inferred=True
            )
        )
    return EvolutionResult(commits, tuple(co_changes), tuple(hotspots), tuple(modules))
