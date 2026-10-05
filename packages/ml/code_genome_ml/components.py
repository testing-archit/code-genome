"""Directory-based component grouping shared by the API and the models."""

from collections import Counter
from pathlib import PurePosixPath


def group_components(paths: list[str], minimum: int = 5, maximum: int = 18) -> dict[str, str]:
    """Group source files by directory at the depth that yields a readable component map.

    Top-level folders are often too coarse (one "src") and leaf folders too fine, so the
    shallowest depth with at least ``minimum`` groups and no group holding over a third of the
    files wins. Groups beyond ``maximum`` fold into "(other)".
    """
    if not paths:
        return {}

    def keyed(depth: int) -> dict[str, str]:
        mapping = {}
        for path in paths:
            parts = PurePosixPath(path).parts[:-1]
            mapping[path] = "/".join(parts[:depth]) if parts else "(root)"
        return mapping

    chosen = keyed(1)
    for depth in range(1, 6):
        mapping = keyed(depth)
        sizes = Counter(mapping.values())
        chosen = mapping
        if len(sizes) >= minimum and max(sizes.values()) <= len(paths) / 3:
            break
        if len(sizes) > maximum:
            break
    sizes = Counter(chosen.values())
    kept = {name for name, _ in sizes.most_common(maximum)}
    return {path: (name if name in kept else "(other)") for path, name in chosen.items()}
