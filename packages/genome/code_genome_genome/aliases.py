"""TypeScript/JavaScript path aliases from tsconfig.json / jsconfig.json ``compilerOptions.paths``.

``"@/*": ["src/*"]`` lets ``import x from "@/lib/x"`` mean ``src/lib/x``. Each config applies to
files under its own directory (monorepos have one per package); the nearest config wins.
Configs are JSON with comments and trailing commas, so those are stripped before parsing.
``extends`` is not followed.
"""

import json
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

CONFIG_NAMES = {"tsconfig.json", "jsconfig.json"}
_STRING = r'"(?:\\.|[^"\\])*"'
_COMMENTS = re.compile(rf"({_STRING})|//[^\n]*|/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA = re.compile(rf"({_STRING})|,(\s*[}}\]])")


@dataclass(frozen=True)
class PathAlias:
    scope: str  # directory of the config ("" for the repository root)
    pattern: str  # e.g. "@/*" or "config"
    targets: tuple[str, ...]  # repository-relative patterns, e.g. ("src/*",)


def _loads_jsonc(text: str) -> object:
    without_comments = _COMMENTS.sub(lambda match: match.group(1) or "", text)
    cleaned = _TRAILING_COMMA.sub(lambda match: match.group(1) or match.group(2), without_comments)
    return json.loads(cleaned)


def parse_config(path: str, text: str) -> list[PathAlias]:
    """Aliases declared by one tsconfig/jsconfig; malformed configs declare none."""
    try:
        data = _loads_jsonc(text)
    except (json.JSONDecodeError, RecursionError):
        return []
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    if not isinstance(options, dict):
        return []
    paths = options.get("paths")
    if not isinstance(paths, dict):
        return []
    scope = str(PurePosixPath(path).parent)
    scope = "" if scope == "." else scope
    declared = options.get("baseUrl")
    base_url = declared if isinstance(declared, str) else "."
    root = posixpath.normpath(posixpath.join(scope or ".", base_url))
    if root.startswith(".."):
        return []
    aliases: list[PathAlias] = []
    for pattern, targets in paths.items():
        if not isinstance(pattern, str) or not isinstance(targets, list):
            continue
        resolved: list[str] = []
        for target in targets:
            if not isinstance(target, str):
                continue
            joined = posixpath.normpath(posixpath.join(root, target))
            if not joined.startswith("..") and not joined.startswith("/"):
                resolved.append("" if joined == "." else joined)
        if resolved and pattern.count("*") <= 1:
            aliases.append(PathAlias(scope, pattern, tuple(resolved)))
    return aliases


def alias_targets(source_path: str, specifier: str, aliases: Sequence[PathAlias]) -> list[str]:
    """Repository-relative bases the specifier may refer to, from the nearest config's aliases."""
    directory = str(PurePosixPath(source_path).parent)
    directory = "" if directory == "." else directory
    applicable = [
        alias
        for alias in aliases
        if not alias.scope or directory == alias.scope or directory.startswith(alias.scope + "/")
    ]
    if not applicable:
        return []
    nearest = max(len(alias.scope) for alias in applicable)
    bases: list[str] = []
    for alias in (item for item in applicable if len(item.scope) == nearest):
        if "*" in alias.pattern:
            prefix, suffix = alias.pattern.split("*", 1)
            if not (specifier.startswith(prefix) and specifier.endswith(suffix)):
                continue
            rest = specifier[len(prefix) : len(specifier) - len(suffix) if suffix else None]
            bases.extend(target.replace("*", rest) for target in alias.targets)
        elif specifier == alias.pattern:
            bases.extend(alias.targets)
    return list(dict.fromkeys(base for base in bases if base and not base.startswith("..")))
