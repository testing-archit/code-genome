"""Learned module discovery with Louvain community detection.

Files are nodes. Import edges weigh 1.0; co-change pairs weigh 0.5 per shared commit
(capped). Louvain maximises modularity; the result is compared with the partition a
reader would guess from top-level directories, so the learned structure is only
preferred when it explains the graph better.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx
from networkx.algorithms.community import louvain_communities, modularity

from .records import ChangeRecord, ImportRecord
from .text import tokenize

MODEL_VERSION = "louvain-import-cochange@1"


@dataclass
class LearnedModule:
    name: str
    files: list[str]
    cohesion: float
    keywords: list[str]


@dataclass
class ModuleDiscovery:
    model_version: str
    status: str
    reason: str | None
    metrics: dict[str, object] = field(default_factory=dict)
    modules: list[LearnedModule] = field(default_factory=list)


def _directory_key(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "(root)"


def discover_modules(
    paths: list[str], changes: list[ChangeRecord], imports: list[ImportRecord]
) -> ModuleDiscovery:
    known = set(paths)
    graph = nx.Graph()
    graph.add_nodes_from(paths)
    for edge in imports:
        if edge.source in known and edge.target in known and edge.source != edge.target:
            weight = graph.get_edge_data(edge.source, edge.target, {"weight": 0.0})["weight"]
            graph.add_edge(edge.source, edge.target, weight=weight + 1.0)
    by_commit: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        if change.path in known:
            by_commit[change.commit_sha].add(change.path)
    for files in by_commit.values():
        if 1 < len(files) <= 20:
            for left, right in combinations(sorted(files), 2):
                weight = graph.get_edge_data(left, right, {"weight": 0.0})["weight"]
                graph.add_edge(left, right, weight=min(weight + 0.5, 6.0))
    connected = [node for node in graph if graph.degree(node) > 0]
    if graph.number_of_edges() < 3 or len(connected) < 4:
        return ModuleDiscovery(
            MODEL_VERSION,
            "insufficient_data",
            "The import and co-change graph is too sparse to cluster.",
        )

    subgraph = graph.subgraph(connected)
    communities = [
        set(group)
        for group in louvain_communities(subgraph, weight="weight", resolution=1.0, seed=7)
    ]
    learned_q = modularity(subgraph, communities, weight="weight")
    shared = min(len(path.split("/")) - 1 for path in connected)
    depth = max(1, shared + 1)
    directory_groups: dict[str, set[str]] = defaultdict(set)
    for node in connected:
        directory_groups[_directory_key(node, depth)].add(node)
    directory_q = modularity(subgraph, list(directory_groups.values()), weight="weight")

    # Name each module by the path tokens most specific to it (a TF-IDF style ratio).
    global_counts: Counter[str] = Counter()
    group_counts = []
    for group in communities:
        counts: Counter[str] = Counter()
        for path in group:
            counts.update(set(tokenize(path.rsplit(".", 1)[0], translate=False, stem=False)))
        group_counts.append(counts)
        global_counts.update(counts)
    modules = []
    for group, counts in zip(communities, group_counts, strict=True):
        ranked = sorted(
            counts,
            key=lambda term: (
                -(counts[term] / len(group)) * (len(communities) / global_counts[term]),
                term,
            ),
        )
        keywords = ranked[:4]
        internal = subgraph.subgraph(group).size(weight="weight")
        total = sum(weight for _, _, weight in subgraph.edges(group, data="weight"))
        modules.append(
            LearnedModule(
                name=" / ".join(keywords[:2]) or "module",
                files=sorted(group),
                cohesion=round(float(internal / total) if total else 0.0, 4),
                keywords=keywords,
            )
        )
    modules.sort(key=lambda module: (-len(module.files), module.name))
    return ModuleDiscovery(
        MODEL_VERSION,
        "trained",
        None,
        metrics={
            "communities": len(communities),
            "clustered_files": len(connected),
            "isolated_files": len(paths) - len(connected),
            "modularity_learned": round(float(learned_q), 4),
            "modularity_directory_baseline": round(float(directory_q), 4),
            "directory_groups": len(directory_groups),
        },
        modules=modules,
    )
