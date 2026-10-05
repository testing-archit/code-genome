"""Repository-level insights derived only from stored snapshot evidence.

* A transparent health score whose every component, weight, and input is returned.
* A module-level dependency graph (imports and co-change aggregated across inferred modules).
* Generated documentation (architecture, modules, data flow, dependencies, business logic,
  risk) assembled from cited facts. Nothing here is model-generated, and each document
  states what it cannot show (for example runtime data flow).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CoChangeEdge,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    KnowledgeChunkRecord,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)
from .knowledge import SKIPPED_DIRECTORIES

HEALTH_VERSION = "health-heuristic@2"
HIGH_RISK = 0.6
DOCS_VERSION = "generated-docs@1"
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|test-d)(/|$)|\.(test|spec)\.[a-z]+$", re.I)
_CODE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}


@dataclass
class RiskFact:
    score: float
    rationale: str
    evidence_ids: list[str]


@dataclass
class SnapshotFacts:
    repository_name: str
    snapshot: RepositorySnapshot
    paths: list[str]
    source_paths: list[str]
    imports: list[tuple[str, str, str]]  # (source file, target key, evidence id)
    modules: list[ModuleCandidate]
    module_of: dict[str, str]
    commits: list[RepositoryCommit]
    risk: dict[str, RiskFact]
    risk_model: str
    hotspots: list[FileHotspot]
    co_changes: list[CoChangeEdge]
    symbols: dict[str, list[str]]
    chunks: list[KnowledgeChunkRecord]
    file_evidence: dict[str, str] = field(default_factory=dict)
    component_of: dict[str, str] = field(default_factory=dict)

    @property
    def internal_imports(self) -> list[tuple[str, str, str]]:
        known = set(self.paths)
        return [item for item in self.imports if item[1] in known]

    @property
    def external_imports(self) -> list[tuple[str, str, str]]:
        return [item for item in self.imports if item[1].startswith("external:")]


def load_facts(
    db: Session,
    snapshot: RepositorySnapshot,
    repository_name: str,
    risk: dict[str, RiskFact],
    risk_model: str,
) -> SnapshotFacts:
    workspace = snapshot.workspace_id
    paths = sorted(
        db.scalars(
            select(FileManifestEntry.path).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == workspace,
            )
        )
    )
    nodes = {
        node.id: node
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id, GraphNode.workspace_id == workspace
            )
        )
    }
    imports: list[tuple[str, str, str]] = []
    for edge in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == workspace,
            GraphEdge.type == "IMPORTS",
        )
    ):
        source, target = nodes.get(edge.from_node), nodes.get(edge.to_node)
        if source is not None and target is not None:
            imports.append((source.natural_key, target.natural_key, edge.provenance_id))
    symbols: dict[str, list[str]] = defaultdict(list)
    file_evidence: dict[str, str] = {}
    for node in nodes.values():
        if node.kind == "SYMBOL":
            name = node.properties_json.get("name")
            path = str(node.properties_json.get("path") or node.natural_key.split("#", 1)[0])
            if isinstance(name, str):
                symbols[path].append(name)
        elif node.kind == "FILE" and node.evidence_ids:
            file_evidence[node.natural_key] = node.evidence_ids[0]
    modules = sorted(
        db.scalars(
            select(ModuleCandidate).where(
                ModuleCandidate.snapshot_id == snapshot.id,
                ModuleCandidate.workspace_id == workspace,
            )
        ),
        key=lambda module: module.natural_key,
    )
    module_of: dict[str, str] = {}
    for module in modules:
        for path in module.file_paths:
            module_of.setdefault(path, module.natural_key)
    commits = list(
        db.scalars(
            select(RepositoryCommit)
            .where(
                RepositoryCommit.repository_id == snapshot.repository_id,
                RepositoryCommit.workspace_id == workspace,
            )
            .order_by(RepositoryCommit.authored_at.desc())
        )
    )
    hotspots = list(
        db.scalars(
            select(FileHotspot)
            .where(FileHotspot.snapshot_id == snapshot.id, FileHotspot.workspace_id == workspace)
            .order_by(FileHotspot.score.desc())
        )
    )
    co_changes = list(
        db.scalars(
            select(CoChangeEdge)
            .where(CoChangeEdge.snapshot_id == snapshot.id, CoChangeEdge.workspace_id == workspace)
            .order_by(CoChangeEdge.commit_count.desc())
        )
    )
    chunks = list(
        db.scalars(
            select(KnowledgeChunkRecord)
            .where(
                KnowledgeChunkRecord.snapshot_id == snapshot.id,
                KnowledgeChunkRecord.workspace_id == workspace,
                KnowledgeChunkRecord.kind.in_(("readme", "doc", "manifest")),
            )
            .order_by(KnowledgeChunkRecord.path, KnowledgeChunkRecord.ordinal)
        )
    )
    source_paths = [
        path
        for path in paths
        if PurePosixPath(path).suffix.lower() in _CODE_SUFFIXES
        and not any(part.lower() in SKIPPED_DIRECTORIES for part in PurePosixPath(path).parts)
    ]
    return SnapshotFacts(
        component_of=components(source_paths),
        repository_name=repository_name,
        snapshot=snapshot,
        paths=paths,
        source_paths=source_paths,
        imports=imports,
        modules=modules,
        module_of=module_of,
        commits=commits,
        risk=risk,
        risk_model=risk_model,
        hotspots=hotspots,
        co_changes=co_changes,
        symbols=dict(symbols),
        chunks=chunks,
        file_evidence=file_evidence,
    )


def components(paths: list[str], minimum: int = 5, maximum: int = 18) -> dict[str, str]:
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


# ---------------------------------------------------------------- health


@dataclass(frozen=True)
class HealthComponent:
    key: str
    label: str
    score: float
    maximum: float
    value: str
    detail: str


def _cycle_files(facts: SnapshotFacts) -> set[str]:
    graph = nx.DiGraph()
    graph.add_edges_from((source, target) for source, target, _ in facts.internal_imports)
    return {
        node
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
        for node in component
    }


def health(facts: SnapshotFacts) -> tuple[int, str, list[HealthComponent]]:
    parts: list[HealthComponent] = []

    population = max(1, len(facts.source_paths) or len(facts.risk))
    risky = sum(1 for item in facts.risk.values() if item.score >= HIGH_RISK)
    share_risky = risky / population
    parts.append(
        HealthComponent(
            "risk",
            "Change risk",
            round(30 * max(0.0, 1 - share_risky / 0.3), 1),
            30,
            f"{risky} of {population} files score ≥ {HIGH_RISK:.0%} ({facts.risk_model})",
            "Full marks when no file is high-risk; zero when 30% or more are. Scores come "
            "from the defect model when trained, otherwise the relative hotspot baseline.",
        )
    )

    in_cycles = _cycle_files(facts)
    linked = {path for source, target, _ in facts.internal_imports for path in (source, target)}
    share = len(in_cycles) / len(linked) if linked else 0.0
    parts.append(
        HealthComponent(
            "coupling",
            "Coupling",
            round(20 * (1 - share), 1),
            20,
            f"{len(in_cycles)} of {len(linked)} linked files in import cycles",
            "Files inside circular import groups are harder to change independently.",
        )
    )

    authors = Counter(commit.author_email.lower() for commit in facts.commits)
    top_share = authors.most_common(1)[0][1] / len(facts.commits) if facts.commits else 1.0
    parts.append(
        HealthComponent(
            "ownership",
            "Knowledge spread",
            round(20 * min(1.0, (1 - top_share) / 0.5), 1),
            20,
            f"{len(authors)} contributors; top author {top_share:.0%} of commits",
            "Full marks when no single author made more than half of the analysed commits.",
        )
    )

    readme = any(item.kind == "readme" for item in facts.chunks)
    doc_files = {item.path for item in facts.chunks if item.kind == "doc"}
    parts.append(
        HealthComponent(
            "docs",
            "Documentation",
            round((8 if readme else 0) + 7 * min(1.0, len(doc_files) / 3), 1),
            15,
            f"README {'present' if readme else 'missing'}; {len(doc_files)} other docs",
            "A README plus at least three further documentation files earn full marks.",
        )
    )

    tests = [path for path in facts.paths if _TEST_PATH.search(path)]
    ratio = len(tests) / len(facts.source_paths) if facts.source_paths else 0.0
    parts.append(
        HealthComponent(
            "tests",
            "Test presence",
            round(15 * min(1.0, ratio / 0.3), 1),
            15,
            f"{len(tests)} test files for {len(facts.source_paths)} source files",
            "Counts test files only; it says nothing about whether the tests pass.",
        )
    )
    total = round(sum(item.score for item in parts))
    band = "Healthy" if total >= 80 else "Moderate" if total >= 60 else "At risk"
    return total, band, parts


# ---------------------------------------------------------------- modules


@dataclass
class ModuleNode:
    name: str
    files: int
    risk: float | None
    fan_in: int
    fan_out: int
    externals: list[str]
    inferred: bool
    description: str
    riskiest: list[str]
    paths: list[str] = field(default_factory=list)


@dataclass
class ModuleLink:
    source: str
    target: str
    imports: int
    co_changes: int
    evidence_ids: list[str]


def group_risk(facts: SnapshotFacts, paths: list[str]) -> tuple[float | None, list[str]]:
    """Mean risk of a group's three riskiest scored files."""
    scored = sorted(
        ((facts.risk[path].score, path) for path in paths if path in facts.risk),
        reverse=True,
    )
    if not scored:
        return None, []
    top = scored[:3]
    return round(sum(score for score, _ in top) / len(top), 4), [path for _, path in top]


def module_graph(facts: SnapshotFacts) -> tuple[list[ModuleNode], list[ModuleLink]]:
    links: dict[tuple[str, str], ModuleLink] = {}
    externals: dict[str, Counter[str]] = defaultdict(Counter)
    group_of = facts.component_of
    for source, target, evidence in facts.imports:
        source_module = group_of.get(source)
        if source_module is None:
            continue
        if target.startswith("external:"):
            externals[source_module][target.removeprefix("external:")] += 1
            continue
        target_module = group_of.get(target)
        if target_module is None or target_module == source_module:
            continue
        link = links.setdefault(
            (source_module, target_module), ModuleLink(source_module, target_module, 0, 0, [])
        )
        link.imports += 1
        if len(link.evidence_ids) < 5:
            link.evidence_ids.append(f"evidence:{evidence}")
    for edge in facts.co_changes:
        left, right = group_of.get(edge.left_path), group_of.get(edge.right_path)
        if left is None or right is None or left == right:
            continue
        key = (
            (left, right) if (left, right) in links or (right, left) not in links else (right, left)
        )
        link = links.setdefault(key, ModuleLink(key[0], key[1], 0, 0, []))
        link.co_changes += edge.commit_count
        if len(link.evidence_ids) < 5 and edge.evidence_shas:
            link.evidence_ids.append(f"commit:{edge.evidence_shas[0]}")
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for link in links.values():
        if link.imports:
            fan_out[link.source] += 1
            fan_in[link.target] += 1
    members: dict[str, list[str]] = defaultdict(list)
    for path, name in group_of.items():
        members[name].append(path)
    nodes = []
    for name, paths in sorted(members.items()):
        risk, riskiest = group_risk(facts, paths)
        symbols = Counter(symbol for path in paths for symbol in facts.symbols.get(path, []))
        nodes.append(
            ModuleNode(
                name=name,
                files=len(paths),
                risk=risk,
                fan_in=fan_in[name],
                fan_out=fan_out[name],
                externals=[package for package, _ in externals[name].most_common(8)],
                inferred=True,
                description=(
                    f"{len(paths)} source files under {name}/"
                    + (
                        "; declares " + ", ".join(sym for sym, _ in symbols.most_common(6))
                        if symbols
                        else ""
                    )
                    + "."
                ),
                riskiest=riskiest,
                paths=sorted(paths)[:200],
            )
        )
    return nodes, sorted(links.values(), key=lambda item: (-item.imports, -item.co_changes))


# ---------------------------------------------------------------- docs


DOCUMENTS = {
    "ARCHITECTURE.md": "System overview, modules, and how they depend on each other",
    "MODULES.md": "Each inferred module: purpose, files, key symbols, dependencies",
    "DATA_FLOW.md": "Entry points and static import flow between files and modules",
    "DEPENDENCIES.md": "External packages and internal module dependencies",
    "BUSINESS_LOGIC.md": "What the project says it does, its features, and domain vocabulary",
    "RISK_REPORT.md": "Health score, risky files and modules, and change hotspots",
}


def _code(value: str) -> str:
    return "`" + value.replace("`", "'") + "`"


def _plain(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def _header(title: str, facts: SnapshotFacts, generated_at: datetime, sources: str) -> list[str]:
    snapshot = facts.snapshot
    return [
        f"# {title}",
        "",
        f"> Generated by CODE GENOME ({DOCS_VERSION}) for {_code(facts.repository_name)} at "
        f"commit {_code(snapshot.commit_sha)} ({snapshot.analysis_version}), "
        f"{generated_at.isoformat()}. Sources: {sources}. Every statement links to stored "
        "evidence; module boundaries are inferred.",
        "",
    ]


def _readme_intro(facts: SnapshotFacts) -> tuple[str, str] | None:
    readmes = sorted(
        (item for item in facts.chunks if item.kind == "readme"),
        key=lambda item: (item.path.count("/"), len(item.path), item.ordinal),
    )
    for chunk in readmes:
        lines = [
            line.strip()
            for line in chunk.text.splitlines()
            if line.strip()
            and not line.lstrip().startswith(("#", "!", "<", "[!", "|", "```"))
            and not re.fullmatch(r"\s*([-*_]\s*){3,}", line)
        ]
        if lines:
            # README lines are often separate sentences without trailing punctuation.
            text = " ".join(
                line if re.search(r"[.!?:;,]$", line) or index == len(lines) - 1 else f"{line}."
                for index, line in enumerate(lines)
            )
            return text[:700], f"evidence:{chunk.provenance_id}"
    return None


def _entry_points(facts: SnapshotFacts) -> list[str]:
    imported = {target for _, target, _ in facts.internal_imports}
    importers = Counter(source for source, _, _ in facts.internal_imports)
    candidates = [
        path
        for path in facts.source_paths
        if path not in imported and importers[path] > 0 and not _TEST_PATH.search(path)
    ]
    return sorted(candidates, key=lambda path: (-importers[path], path))[:8]


def _architecture(
    facts: SnapshotFacts, nodes: list[ModuleNode], links: list[ModuleLink]
) -> list[str]:
    lines = ["## What it is", ""]
    intro = _readme_intro(facts)
    lines += [f"{intro[0]} ({intro[1]})" if intro else "No README description was found.", ""]
    lines += [
        "## At a glance",
        "",
        f"- {len(facts.paths)} files, {len(facts.source_paths)} JS/TS source files",
        f"- {len(facts.modules)} inferred modules, {len(facts.internal_imports)} internal imports",
        f"- {len({c.author_email.lower() for c in facts.commits})} contributors across "
        f"{len(facts.commits)} analysed commits",
        "",
        "## Modules",
        "",
        "| Module | Files | Depends on | Used by | Risk |",
        "|---|---|---|---|---|",
    ]
    for node in sorted(nodes, key=lambda item: -item.files):
        risk = f"{node.risk:.0%}" if node.risk is not None else "—"
        lines.append(
            f"| {_code(node.name)} | {node.files} | {node.fan_out} | {node.fan_in} | {risk} |"
        )
    lines += ["", "## Module dependencies", ""]
    lines += [
        f"- {_code(link.source)} → {_code(link.target)}: {link.imports} imports"
        + (f", co-changed {link.co_changes}×" if link.co_changes else "")
        + f" ({', '.join(link.evidence_ids[:2])})"
        for link in links[:30]
    ] or ["- No cross-module imports were observed."]
    lines += ["", "## Entry points", ""]
    lines += [
        f"- {_code(path)} imports {sum(1 for s, _, _ in facts.internal_imports if s == path)} "
        "files and is imported by none"
        + (f" (evidence:{facts.file_evidence[path]})" if path in facts.file_evidence else "")
        for path in _entry_points(facts)
    ] or ["- No entry points were detected from imports."]
    return lines


def _modules_doc(
    facts: SnapshotFacts, nodes: list[ModuleNode], links: list[ModuleLink]
) -> list[str]:
    members: dict[str, list[str]] = defaultdict(list)
    for path, name in facts.component_of.items():
        members[name].append(path)
    lines = [
        "Components group source files by directory; inferred modules (end of document) come "
        "from directory structure and co-change history.",
        "",
    ]
    for node in sorted(nodes, key=lambda item: -item.files):
        paths = sorted(members[node.name])
        uses = [link.target for link in links if link.source == node.name and link.imports]
        used_by = [link.source for link in links if link.target == node.name and link.imports]
        symbols = Counter(name for path in paths for name in facts.symbols.get(path, []))
        lines += [
            f"## {_code(node.name)}",
            "",
            f"- Files ({node.files}): "
            + ", ".join(
                _code(path)
                + (
                    f" (evidence:{facts.file_evidence[path]})"
                    if path in facts.file_evidence
                    else ""
                )
                for path in paths[:20]
            )
            + (" …" if node.files > 20 else ""),
            "- Key symbols: "
            + (", ".join(_code(name) for name, _ in symbols.most_common(15)) or "none"),
            "- Depends on: " + (", ".join(_code(name) for name in uses) or "no other component"),
            "- Used by: " + (", ".join(_code(name) for name in used_by) or "no other component"),
            "- External packages: " + (", ".join(_code(name) for name in node.externals) or "none"),
            "- Riskiest files: "
            + (
                ", ".join(f"{_code(path)} ({facts.risk[path].score:.0%})" for path in node.riskiest)
                or "no risk scores"
            ),
            "",
        ]
    if not nodes:
        lines.append("No JS/TS source components were found.")
    lines += ["## Inferred modules", ""]
    lines += [
        f"- {_code(module.natural_key)} ({len(module.file_paths)} files, confidence "
        f"{module.confidence:.2f}): {_plain(module.description)}"
        + (f" (commit:{module.evidence_shas[0]})" if module.evidence_shas else "")
        for module in facts.modules
    ] or ["- No modules were inferred."]
    return lines


def _data_flow(facts: SnapshotFacts, links: list[ModuleLink]) -> list[str]:
    lines = [
        "This document follows static imports only. Runtime data flow (network calls, events, "
        "database reads) is not traced.",
        "",
        "## Module flow",
        "",
    ]
    lines += [
        f"- {_code(link.source)} → {_code(link.target)} ({link.imports} imports)"
        for link in links
        if link.imports
    ][:30] or ["- No cross-module imports were observed."]
    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source, target, evidence in facts.internal_imports:
        graph[source].append((target, evidence))
    lines += ["", "## From each entry point", ""]
    for entry in _entry_points(facts)[:5]:
        lines.append(f"### {_code(entry)}")
        lines.append("")
        seen = {entry}
        frontier = [(entry, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= 3:
                continue
            for target, evidence in sorted(graph.get(current, []))[:8]:
                if target in seen:
                    continue
                seen.add(target)
                lines.append(f"{'  ' * depth}- {_code(target)} (evidence:{evidence})")
                frontier.append((target, depth + 1))
        lines.append("")
    if not _entry_points(facts):
        lines.append("No entry points were detected from imports.")
    return lines


def _dependencies(facts: SnapshotFacts, links: list[ModuleLink]) -> list[str]:
    usage: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, str] = {}
    for source, target, provenance in facts.external_imports:
        name = target.removeprefix("external:")
        package = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
        usage[package].add(source)
        evidence.setdefault(package, provenance)
    lines = [
        "## External packages (by importing files)",
        "",
        "| Package | Files | Evidence |",
        "|---|---|---|",
    ]
    lines += [
        f"| {_code(name)} | {len(files)} | evidence:{evidence[name]} |"
        for name, files in sorted(usage.items(), key=lambda item: (-len(item[1]), item[0]))[:60]
    ]
    manifests = [item for item in facts.chunks if PurePosixPath(item.path).name == "package.json"]
    if manifests:
        lines += ["", "## Declared in manifests", ""]
        lines += [
            f"- {_code(item.path)}: {_plain(item.text)[:600]} (evidence:{item.provenance_id})"
            for item in manifests[:6]
        ]
    lines += ["", "## Internal module dependencies", ""]
    lines += [
        f"- {_code(link.source)} depends on {_code(link.target)} ({link.imports} imports)"
        for link in links
        if link.imports
    ][:40] or ["- No cross-module imports were observed."]
    return lines


_GENERIC_WORDS = {
    "get", "set", "use", "create", "update", "delete", "handle", "on", "is", "has", "to",
    "from", "with", "for", "the", "of", "and", "init", "props", "type", "types", "default",
    "data", "value", "item", "items", "list", "new", "make", "build", "render", "test",
    "options", "config", "error", "request", "response", "state", "context", "provider",
}  # fmt: skip


def _business(facts: SnapshotFacts, nodes: list[ModuleNode]) -> list[str]:
    lines = [
        "Assembled from the project's own README and docs plus code vocabulary. It records "
        "what the project says it does; it does not verify that the code does it.",
        "",
        "## Purpose",
        "",
    ]
    intro = _readme_intro(facts)
    lines += [f"{intro[0]} ({intro[1]})" if intro else "No README description was found.", ""]
    lines += ["## Documented features and topics", ""]
    sections = [
        item
        for item in facts.chunks
        if item.kind in {"readme", "doc"} and item.heading and item.path.count("/") <= 1
    ]
    for item in sections[:40]:
        body = " ".join(
            line.strip()
            for line in item.text.splitlines()[1:]
            if line.strip() and not line.strip().startswith(("```", "|", "<", "!"))
        )
        lines.append(
            f"- **{_plain(item.heading)}** ({_code(item.path)} lines {item.start_line}-"
            f"{item.end_line}, evidence:{item.provenance_id}): {_plain(body)[:240]}"
        )
    if not sections:
        lines.append("- No documented sections were found.")
    words: Counter[str] = Counter()
    for names in facts.symbols.values():
        for name in names:
            for word in re.findall(r"[A-Z]?[a-z]{3,}", name):
                lowered = word.lower()
                if lowered not in _GENERIC_WORDS:
                    words[lowered] += 1
    lines += [
        "",
        "## Domain vocabulary (most frequent words in declared symbol names)",
        "",
        ", ".join(f"{word} ({count})" for word, count in words.most_common(30)) or "none",
        "",
        "## Where the logic lives (inferred modules)",
        "",
    ]
    lines += [f"- {_code(node.name)}: {_plain(node.description)}" for node in nodes] or ["- none"]
    return lines


def _risk_report(
    facts: SnapshotFacts,
    nodes: list[ModuleNode],
    health_result: tuple[int, str, list[HealthComponent]],
) -> list[str]:
    score, band, components = health_result
    lines = [
        f"## Health score: {score}/100 ({band})",
        "",
        f"Heuristic ({HEALTH_VERSION}); a prompt for review, not a quality guarantee.",
        "",
        "| Component | Score | Input |",
        "|---|---|---|",
    ]
    lines += [
        f"| {item.label} | {item.score:g}/{item.maximum:g} | {item.value} |" for item in components
    ]
    lines += [
        "",
        f"## Riskiest files ({facts.risk_model})",
        "",
        "| File | Risk | Why | Evidence |",
        "|---|---|---|---|",
    ]
    ranked = sorted(facts.risk.items(), key=lambda item: -item[1].score)[:25]
    lines += [
        f"| {_code(path)} | {fact.score:.0%} | {_plain(fact.rationale)[:160]} | "
        f"{', '.join(fact.evidence_ids[:2]) or 'none'} |"
        for path, fact in ranked
    ] or ["No risk scores are available."]
    lines += ["", "## Riskiest modules", ""]
    lines += [
        f"- {_code(node.name)}: {node.risk:.0%} (mean of its riskiest files: "
        + ", ".join(_code(path) for path in node.riskiest)
        + ")"
        for node in sorted(nodes, key=lambda item: -(item.risk or 0))
        if node.risk is not None
    ][:10] or ["- No module risk could be computed."]
    lines += ["", "## Change hotspots", ""]
    lines += [
        f"- {_code(item.path)}: {item.commit_count} commits, churn {item.churn} "
        f"(commit:{item.evidence_shas[0] if item.evidence_shas else 'none'})"
        for item in facts.hotspots[:15]
    ] or ["- No hotspots were recorded."]
    return lines


def generate_documents(facts: SnapshotFacts, generated_at: datetime) -> dict[str, str]:
    nodes, links = module_graph(facts)
    health_result = health(facts)
    sources = "file manifest, import graph, inferred modules, commit history"
    bodies = {
        "ARCHITECTURE.md": (
            "Architecture",
            sources + ", README",
            _architecture(facts, nodes, links),
        ),
        "MODULES.md": ("Modules", sources, _modules_doc(facts, nodes, links)),
        "DATA_FLOW.md": ("Data flow", "import graph", _data_flow(facts, links)),
        "DEPENDENCIES.md": (
            "Dependencies",
            "import graph, package manifests",
            _dependencies(facts, links),
        ),
        "BUSINESS_LOGIC.md": (
            "Business logic",
            "README and docs, declared symbols, inferred modules",
            _business(facts, nodes),
        ),
        "RISK_REPORT.md": (
            "Risk report",
            "risk model, hotspots, import graph, commit history",
            _risk_report(facts, nodes, health_result),
        ),
    }
    return {
        name: "\n".join([*_header(title, facts, generated_at, source), *body]).rstrip() + "\n"
        for name, (title, source, body) in bodies.items()
    }
