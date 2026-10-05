"""Unified Software Genome Graph, SZZ bug history, and evolution timeline.

Everything here is assembled from stored, snapshot-scoped evidence: graph nodes/edges from the
structural analyzer, file changes and co-change from history mining, SZZ-lite bug links, and
static data-store/integration detection. Relationships that are not directly observed
(semantic similarity, candidate calls, bug introduction, data access direction) carry
``inferred: true``.
"""

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

import numpy as np
from code_genome_git import SZZ_VERSION, is_fix_message
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import GraphEdge, GraphNode, RepositoryCommit
from . import embeddings
from .insights import (
    SnapshotFacts,
    architecture_facts,
    module_graph,
    package_name,
)

GENOME_VERSION = "genome-graph@1"
TIMELINE_VERSION = "timeline@1"
FIX_RULE = (
    "Commit subject matches the word-bounded keywords fix, bug, regression, hotfix, patch, "
    "crash, or error (and inflections); merge commits excluded."
)
NO_EVIDENCE = "No supporting evidence was identified in the selected scope."
SEMANTIC_THRESHOLD = 0.6
SEMANTIC_TOP_K = 3
MAX_SEMANTIC_FILES = 3_000
# Sentence embeddings of identifier text score related files higher than LSA does.
TRANSFORMER_THRESHOLD = 0.7
RECENT_COMMITS = 25
MAX_COMMITS = 80
MAX_DEVELOPERS = 25
MAX_EXTERNALS = 30


def _hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def developer_id(email: str) -> str:
    return f"developer:{_hash(email.strip().lower())}"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _subject(message: str) -> str:
    stripped = message.strip()
    return stripped.splitlines()[0][:200] if stripped else ""


@dataclass
class Node:
    id: str
    kind: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    inferred: bool = False


@dataclass
class Edge:
    id: str
    kind: str
    source: str
    target: str
    weight: float = 1.0
    confidence: float = 1.0
    inferred: bool = False
    evidence_ids: list[str] = field(default_factory=list)


class GenomeGraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.semantic_backend = LSA_BACKEND

    def node(self, node: Node) -> None:
        self.nodes.setdefault(node.id, node)

    def edge(
        self,
        kind: str,
        source: str,
        target: str,
        *,
        weight: float = 1.0,
        confidence: float = 1.0,
        inferred: bool = False,
        evidence_ids: Iterable[str] = (),
    ) -> None:
        if source == target or source not in self.nodes or target not in self.nodes:
            return
        edge_id = f"gedge_{_hash(f'{kind}|{source}|{target}', 24)}"
        current = self.edges.get(edge_id)
        evidence = list(dict.fromkeys(evidence_ids))
        if current is None:
            self.edges[edge_id] = Edge(
                edge_id, kind, source, target, weight, confidence, inferred, evidence[:5]
            )
            return
        current.weight += weight
        current.confidence = max(current.confidence, confidence)
        current.evidence_ids = list(dict.fromkeys([*current.evidence_ids, *evidence]))[:5]


# ---------------------------------------------------------------- semantic similarity

_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _SPLIT.findall(text) if len(token) > 1]


LSA_BACKEND = "lsa"


def _top_pairs(
    paths: list[str], unit: np.ndarray, threshold: float, top_k: int
) -> list[tuple[str, str, float]]:
    similarity = unit @ unit.T
    np.fill_diagonal(similarity, -1.0)
    pairs: dict[tuple[str, str], float] = {}
    for index, path in enumerate(paths):
        row = similarity[index]
        for other in np.argsort(-row)[:top_k]:
            score = float(row[other])
            if score < threshold:
                break
            left, right = sorted((path, paths[int(other)]))
            pairs[(left, right)] = max(pairs.get((left, right), 0.0), round(score, 4))
    return sorted(((left, right, score) for (left, right), score in pairs.items()))


def semantic_pairs(
    documents: dict[str, str],
    *,
    threshold: float = SEMANTIC_THRESHOLD,
    top_k: int = SEMANTIC_TOP_K,
) -> list[tuple[str, str, float]]:
    """LSA (TF-IDF + truncated SVD) similarity between files; top-k pairs per file."""
    paths = sorted(documents)[:MAX_SEMANTIC_FILES]
    if len(paths) < 4:
        return []
    corpus = [documents[path] for path in paths]
    try:
        matrix = TfidfVectorizer(token_pattern=r"[a-z0-9]{2,}", sublinear_tf=True).fit_transform(
            corpus
        )
    except ValueError:  # empty vocabulary
        return []
    components = min(64, len(paths) - 1, matrix.shape[1] - 1)
    if components < 2:
        return []
    reduced = TruncatedSVD(n_components=components, random_state=0).fit_transform(matrix)
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return _top_pairs(paths, reduced / norms, threshold, top_k)


def semantic_pairs_with_backend(
    documents: dict[str, str], cache_key: tuple[str, str, str]
) -> tuple[list[tuple[str, str, float]], str]:
    """Transformer embeddings when available (see ``embeddings``), else LSA; returns the pairs
    and the backend that produced them."""
    paths = sorted(documents)[:MAX_SEMANTIC_FILES]
    if len(paths) >= 4 and embeddings.transformer_requested():
        vectors = embeddings.unit_vectors(cache_key, paths, [documents[path] for path in paths])
        if vectors is not None:
            pairs = _top_pairs(paths, vectors, TRANSFORMER_THRESHOLD, SEMANTIC_TOP_K)
            return pairs, f"{embeddings.TRANSFORMER_BACKEND}:{embeddings.model_name()}"
    return semantic_pairs(documents), LSA_BACKEND


def _semantic_documents(facts: SnapshotFacts, paths: Iterable[str]) -> dict[str, str]:
    documents = {}
    for path in paths:
        words = _tokens(" ".join(PurePosixPath(path).with_suffix("").parts))
        for name in facts.symbols.get(path, [])[:200]:
            words += _tokens(name)
        documents[path] = " ".join(words)
    return documents


# ---------------------------------------------------------------- genome graph


def _commit_node(commit: RepositoryCommit) -> Node:
    fix = is_fix_message(commit.message) and len(commit.parent_shas or []) <= 1
    return Node(
        id=f"commit:{commit.sha}",
        kind="commit",
        label=_subject(commit.message) or commit.sha[:12],
        properties={
            "sha": commit.sha,
            "short_sha": commit.sha[:12],
            "subject": _subject(commit.message),
            "author": commit.author_name,
            "authored_at": _as_utc(commit.authored_at).isoformat(),
            "is_fix": fix,
        },
        evidence_ids=[f"commit:{commit.sha}"],
    )


def build_full_genome(
    db: Session, facts: SnapshotFacts, *, include_symbols_for: set[str] | None = None
) -> GenomeGraphBuilder:
    """Assemble the complete typed graph for a snapshot (callers bound and focus it)."""
    graph = GenomeGraphBuilder()
    snapshot = facts.snapshot
    nodes_modules, links = module_graph(facts)
    architecture = architecture_facts(facts)
    commit_by_sha = {commit.sha: commit for commit in facts.commits}
    introduced_files: Counter[str] = Counter()
    for link in facts.bug_links:
        introduced_files[link.path] += 1

    # Components and files.
    for module in nodes_modules:
        graph.node(
            Node(
                id=f"component:{module.name}",
                kind="component",
                label=module.name,
                properties={
                    "name": module.name,
                    "role": module.role,
                    "role_signal": module.role_signal,
                    "files": module.files,
                    "risk": module.risk,
                    "commits": module.commits,
                    "bug_fixes": module.bug_fixes,
                },
                inferred=True,
            )
        )
    for path, component in sorted(facts.component_of.items()):
        properties = facts.file_properties.get(path, {})
        risk = facts.risk.get(path)
        graph.node(
            Node(
                id=f"file:{path}",
                kind="file",
                label=PurePosixPath(path).name,
                properties={
                    "path": path,
                    "component": component,
                    "language": properties.get("language"),
                    "loc": properties.get("loc"),
                    "complexity": properties.get("complexity"),
                    "functions": properties.get("functions"),
                    "risk": risk.score if risk else None,
                    "bug_links": introduced_files.get(path, 0),
                },
                evidence_ids=[f"evidence:{facts.file_evidence[path]}"]
                if path in facts.file_evidence
                else [],
            )
        )
        graph.edge(
            "BELONGS_TO_MODULE",
            f"file:{path}",
            f"component:{component}",
            inferred=True,
            evidence_ids=[f"evidence:{facts.file_evidence[path]}"]
            if path in facts.file_evidence
            else [],
        )

    # Static imports and component dependencies.
    package_use: Counter[str] = Counter()
    for source, target, _evidence in facts.external_imports:
        package = package_name(target)
        if package and source in facts.component_of:
            package_use[package] += 1
    for package, count in package_use.most_common(MAX_EXTERNALS * 4):
        graph.node(
            Node(
                id=f"external:{package}",
                kind="external",
                label=package,
                properties={"package": package, "importing_files": count},
            )
        )
    for source, target, evidence in facts.imports:
        if target.startswith("external:"):
            package = package_name(target)
            if package:
                graph.edge(
                    "IMPORTS",
                    f"file:{source}",
                    f"external:{package}",
                    evidence_ids=[f"evidence:{evidence}"],
                )
        else:
            graph.edge(
                "IMPORTS", f"file:{source}", f"file:{target}", evidence_ids=[f"evidence:{evidence}"]
            )
    for module_link in links:
        if module_link.imports:
            graph.edge(
                "DEPENDS_ON",
                f"component:{module_link.source}",
                f"component:{module_link.target}",
                weight=module_link.imports,
                evidence_ids=[
                    item for item in module_link.evidence_ids if item.startswith("evidence:")
                ],
            )

    # Candidate calls: symbol level for focused files, collapsed to files otherwise.
    symbol_nodes = {
        node.id: node
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
                GraphNode.kind == "SYMBOL",
            )
        )
    }
    focus_files = include_symbols_for or set()

    def symbol_path(node: GraphNode) -> str:
        return str(node.properties_json.get("path") or node.natural_key.split("#", 1)[0])

    def add_symbol(node: GraphNode) -> str:
        properties = node.properties_json or {}
        kind = "class" if properties.get("symbol_kind") == "class" else "function"
        node_id = f"symbol:{node.id}"
        path = symbol_path(node)
        graph.node(
            Node(
                id=node_id,
                kind=kind,
                label=str(properties.get("name", node.natural_key)),
                properties={
                    "name": properties.get("name"),
                    "symbol_kind": properties.get("symbol_kind"),
                    "path": path,
                    "exported": properties.get("exported"),
                    "span": properties.get("span"),
                },
                evidence_ids=[f"evidence:{item}" for item in node.evidence_ids[:2]],
            )
        )
        graph.edge(
            "DECLARES",
            f"file:{path}",
            node_id,
            evidence_ids=[f"evidence:{item}" for item in node.evidence_ids[:1]],
        )
        return node_id

    for node in symbol_nodes.values():
        if symbol_path(node) in focus_files and (node.properties_json or {}).get("symbol_kind") in {
            "function",
            "class",
            "method",
        }:
            add_symbol(node)
    file_nodes = {
        node.id: node.natural_key
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
                GraphNode.kind == "FILE",
            )
        )
    }
    for call in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == snapshot.workspace_id,
            GraphEdge.type == "CALLS",
        )
    ):
        call_evidence = [f"evidence:{call.provenance_id}"]
        ends: list[tuple[str, GraphNode | None]] = []
        for node_id in (call.from_node, call.to_node):
            if node_id in symbol_nodes:
                ends.append((symbol_path(symbol_nodes[node_id]), symbol_nodes[node_id]))
            elif node_id in file_nodes:
                ends.append((file_nodes[node_id], None))
        if len(ends) != 2:
            continue
        (source_path, source_symbol), (target_path, target_symbol) = ends
        if focus_files & {source_path, target_path}:
            source = add_symbol(source_symbol) if source_symbol else f"file:{source_path}"
            target = add_symbol(target_symbol) if target_symbol else f"file:{target_path}"
            graph.edge(
                "CALLS",
                source,
                target,
                confidence=call.confidence,
                inferred=call.confidence < 1.0,
                evidence_ids=call_evidence,
            )
        if source_path != target_path:
            graph.edge(
                "CALLS",
                f"file:{source_path}",
                f"file:{target_path}",
                confidence=call.confidence,
                inferred=call.confidence < 1.0,
                evidence_ids=call_evidence,
            )

    # Co-change.
    for co_change in facts.co_changes:
        graph.edge(
            "CO_CHANGED_WITH",
            f"file:{co_change.left_path}",
            f"file:{co_change.right_path}",
            weight=co_change.commit_count,
            confidence=co_change.confidence,
            evidence_ids=[f"commit:{sha}" for sha in co_change.evidence_shas[:3]],
        )

    # Semantic similarity (inferred).
    semantic, graph.semantic_backend = semantic_pairs_with_backend(
        _semantic_documents(facts, facts.component_of),
        (snapshot.workspace_id, snapshot.id, snapshot.analysis_version),
    )
    for left, right, score in semantic:
        graph.edge(
            "SEMANTICALLY_RELATED_TO",
            f"file:{left}",
            f"file:{right}",
            weight=score,
            confidence=score,
            inferred=True,
            evidence_ids=[
                f"evidence:{facts.file_evidence[path]}"
                for path in (left, right)
                if path in facts.file_evidence
            ],
        )

    # Commits: recent, every fix commit, and every commit named by a bug link.
    bug_shas = {link.fix_sha for link in facts.bug_links} | {
        link.introducing_sha for link in facts.bug_links
    }
    ordered = sorted(facts.commits, key=lambda item: _as_utc(item.authored_at), reverse=True)
    chosen = [
        commit
        for index, commit in enumerate(ordered)
        if index < RECENT_COMMITS
        or commit.sha in bug_shas
        or (is_fix_message(commit.message) and len(commit.parent_shas or []) <= 1)
    ][:MAX_COMMITS]
    for commit in chosen:
        graph.node(_commit_node(commit))

    # Developers and authorship.
    author_commits: Counter[str] = Counter(commit.author_email.lower() for commit in facts.commits)
    author_names = {commit.author_email.lower(): commit.author_name for commit in facts.commits}
    for email, count in author_commits.most_common(MAX_DEVELOPERS):
        graph.node(
            Node(
                id=developer_id(email),
                kind="developer",
                label=author_names[email],
                properties={"name": author_names[email], "commits": count},
            )
        )
    for commit in chosen:
        graph.edge(
            "AUTHORED_BY",
            f"commit:{commit.sha}",
            developer_id(commit.author_email),
            evidence_ids=[f"commit:{commit.sha}"],
        )

    # File history: MODIFIED_BY, FIXED_BY, OWNED_BY.
    file_authors: dict[str, Counter[str]] = defaultdict(Counter)
    file_author_shas: dict[tuple[str, str], list[str]] = defaultdict(list)
    for change in facts.file_changes:
        changed = commit_by_sha.get(change.commit_sha)
        if changed is None or change.path not in facts.component_of:
            continue
        email = changed.author_email.lower()
        file_authors[change.path][email] += 1
        file_author_shas[(change.path, email)].append(changed.sha)
        graph.edge(
            "MODIFIED_BY",
            f"file:{change.path}",
            f"commit:{changed.sha}",
            weight=change.churn,
            evidence_ids=[f"commit:{changed.sha}"],
        )
        if is_fix_message(changed.message) and len(changed.parent_shas or []) <= 1:
            graph.edge(
                "FIXED_BY",
                f"file:{change.path}",
                f"commit:{changed.sha}",
                inferred=True,
                evidence_ids=[f"commit:{changed.sha}"],
            )
    for path, authors in file_authors.items():
        email, count = sorted(authors.items(), key=lambda item: (-item[1], item[0]))[0]
        total = sum(authors.values())
        graph.edge(
            "OWNED_BY",
            f"file:{path}",
            developer_id(email),
            weight=round(count / total, 4),
            confidence=round(count / total, 4),
            inferred=True,
            evidence_ids=[f"commit:{sha}" for sha in file_author_shas[(path, email)][:3]],
        )
    component_shas: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for (path, email), author_shas in file_author_shas.items():
        component_shas[facts.component_of[path]][email].update(author_shas)
    for name, by_author in component_shas.items():
        email, owner_shas = sorted(by_author.items(), key=lambda item: (-len(item[1]), item[0]))[0]
        total = len(set().union(*by_author.values()))
        share = round(len(owner_shas) / max(1, total), 4)
        graph.edge(
            "OWNED_BY",
            f"component:{name}",
            developer_id(email),
            weight=share,
            confidence=share,
            inferred=True,
            evidence_ids=[f"commit:{sha}" for sha in sorted(owner_shas)[:3]],
        )

    # SZZ bug links.
    for link in facts.bug_links:
        link_evidence = [
            f"evidence:{link.provenance_id}",
            f"commit:{link.introducing_sha}",
            f"commit:{link.fix_sha}",
        ]
        graph.edge(
            "INTRODUCED_BUG",
            f"commit:{link.introducing_sha}",
            f"file:{link.path}",
            weight=link.lines,
            confidence=link.confidence,
            inferred=True,
            evidence_ids=link_evidence,
        )
        graph.edge(
            "FIXED_BY",
            f"file:{link.path}",
            f"commit:{link.fix_sha}",
            inferred=True,
            evidence_ids=link_evidence,
        )

    # Data stores and external services (inferred from imports and literal URLs).
    for name, stores in architecture.datastores.items():
        for store in stores:
            store_id = f"datastore:{store['name']}"
            graph.node(
                Node(
                    id=store_id,
                    kind="datastore",
                    label=store["name"],
                    properties={"name": store["name"], "packages": store["packages"]},
                    evidence_ids=store["evidence_ids"][:3],
                    inferred=True,
                )
            )
            kinds = {
                "read": ["READS_FROM"],
                "write": ["WRITES_TO"],
                "read_write": ["READS_FROM", "WRITES_TO"],
            }.get(store["access"], ["USES_DATASTORE"])
            for kind in kinds:
                graph.edge(
                    kind,
                    f"component:{name}",
                    store_id,
                    weight=len(store["files"]),
                    confidence=0.6 if store["via"] == "direct" else 0.45,
                    inferred=True,
                    evidence_ids=store["evidence_ids"],
                )
    for name, services in architecture.integrations.items():
        for service in services:
            service_id = f"external_api:{service['name']}"
            graph.node(
                Node(
                    id=service_id,
                    kind="external_api",
                    label=service["name"],
                    properties={
                        "name": service["name"],
                        "package": service["package"],
                        "host": service["host"],
                    },
                    evidence_ids=service["evidence_ids"][:3],
                    inferred=True,
                )
            )
            graph.edge(
                "CALLS_API",
                f"component:{name}",
                service_id,
                weight=len(service["files"]),
                confidence=0.6,
                inferred=True,
                evidence_ids=service["evidence_ids"],
            )
    return graph


_KIND_PRIORITY = {
    "component": 0,
    "datastore": 1,
    "external_api": 1,
    "file": 2,
    "function": 3,
    "class": 3,
    "developer": 4,
    "commit": 5,
    "external": 6,
}


@dataclass
class GenomeView:
    nodes: list[Node]
    edges: list[Edge]
    total_nodes: int
    total_edges: int
    truncated: bool
    focus_node_id: str | None


def resolve_focus(facts: SnapshotFacts, focus: str) -> tuple[str, set[str]] | None:
    """Map a focus string to a node id and the files whose symbols should be expanded."""
    value = focus.strip().strip("/")
    if not value:
        return None
    members: dict[str, set[str]] = defaultdict(set)
    for path, name in facts.component_of.items():
        members[name].add(path)
    if value in members:
        return f"component:{value}", members[value] if len(members[value]) <= 40 else set()
    if value in facts.component_of:
        return f"file:{value}", {value}
    return None


def select_view(
    graph: GenomeGraphBuilder, *, limit: int, focus_node_id: str | None = None
) -> GenomeView:
    nodes, edges = graph.nodes, graph.edges
    if focus_node_id is not None:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges.values():
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
        distance = {focus_node_id: 0}
        frontier = [focus_node_id]
        for depth in (1, 2):
            next_frontier = []
            for current in frontier:
                for neighbour in sorted(adjacency[current]):
                    if neighbour not in distance:
                        distance[neighbour] = depth
                        next_frontier.append(neighbour)
            frontier = next_frontier
        ranked = sorted(
            distance,
            key=lambda node_id: (
                distance[node_id],
                _KIND_PRIORITY.get(nodes[node_id].kind, 9),
                node_id,
            ),
        )
    else:
        degree: Counter[str] = Counter()
        for edge in edges.values():
            degree[edge.source] += 1
            degree[edge.target] += 1
        ranked = sorted(
            (node_id for node_id, node in nodes.items() if node.kind not in {"function", "class"}),
            key=lambda node_id: (
                _KIND_PRIORITY.get(nodes[node_id].kind, 9),
                -degree[node_id],
                node_id,
            ),
        )
    candidates = len(ranked)
    kept = set(ranked[:limit])
    chosen_edges = [edge for edge in edges.values() if edge.source in kept and edge.target in kept]
    return GenomeView(
        nodes=[nodes[node_id] for node_id in ranked[:limit]],
        edges=sorted(chosen_edges, key=lambda item: (item.kind, item.source, item.target)),
        total_nodes=candidates,
        total_edges=len(edges),
        truncated=candidates > limit,
        focus_node_id=focus_node_id,
    )


# ---------------------------------------------------------------- bug history


def bug_history(
    facts: SnapshotFacts, *, path: str | None = None, limit: int = 60
) -> dict[str, Any]:
    commit_by_sha = {commit.sha: commit for commit in facts.commits}
    files_by_sha: dict[str, set[str]] = defaultdict(set)
    for change in facts.file_changes:
        files_by_sha[change.commit_sha].add(change.path)
    links = [link for link in facts.bug_links if path is None or link.path == path]
    fix_commits = sorted(
        (
            commit
            for commit in facts.commits
            if is_fix_message(commit.message) and len(commit.parent_shas or []) <= 1
        ),
        key=lambda item: _as_utc(item.authored_at),
        reverse=True,
    )
    linked_fixes = {link.fix_sha for link in links}
    fixes = [
        commit
        for commit in fix_commits
        if path is None or commit.sha in linked_fixes or path in files_by_sha.get(commit.sha, ())
    ][:limit]
    links_by_fix: dict[str, list[Any]] = defaultdict(list)
    for link in links:
        links_by_fix[link.fix_sha].append(link)

    def describe(sha: str) -> dict[str, Any]:
        commit = commit_by_sha.get(sha)
        return {
            "subject": _subject(commit.message) if commit else None,
            "author": commit.author_name if commit else None,
            "authored_at": _as_utc(commit.authored_at) if commit else None,
        }

    fix_items = []
    for commit in fixes:
        introducing = [
            {
                "introducing_sha": link.introducing_sha,
                **describe(link.introducing_sha),
                "path": link.path,
                "lines": link.lines,
                "confidence": link.confidence,
                "evidence_id": f"evidence:{link.provenance_id}",
                "evidence": link.evidence_json,
                "bulk_commit": bool(link.evidence_json.get("bulk_introducing_commit")),
                "shallow_boundary": bool(link.evidence_json.get("shallow_boundary")),
            }
            for link in sorted(
                links_by_fix.get(commit.sha, []), key=lambda item: (-item.lines, item.path)
            )
        ]
        fix_items.append(
            {
                "fix_sha": commit.sha,
                **describe(commit.sha),
                "files": sorted(files_by_sha.get(commit.sha, set()))[:50],
                "introducing": introducing,
            }
        )
    per_file: dict[str, dict[str, Any]] = {}
    for link in links:
        entry = per_file.setdefault(
            link.path,
            {"path": link.path, "fix_shas": [], "introducing_shas": [], "lines": 0},
        )
        if link.fix_sha not in entry["fix_shas"]:
            entry["fix_shas"].append(link.fix_sha)
        if link.introducing_sha not in entry["introducing_shas"]:
            entry["introducing_shas"].append(link.introducing_sha)
        entry["lines"] += link.lines
    file_items = [
        {
            **entry,
            "fix_commits": len(entry["fix_shas"]),
            "introducing_commits": len(entry["introducing_shas"]),
        }
        for entry in sorted(
            per_file.values(), key=lambda item: (-len(item["fix_shas"]), item["path"])
        )
    ][:200]
    limitations = [
        f"SZZ is heuristic ({SZZ_VERSION}): introducing commits are candidates, not proof. "
        "Refactors, moved code, and fixes that only add lines distort the result.",
        f"Fix commits are classified by a keyword rule: {FIX_RULE}",
        "Only the most recent fix commits in the analysed (possibly shallow) history were "
        "traced; candidates at the shallow-history boundary or in bulk commits have lower "
        "confidence.",
    ]
    if not fix_items and not file_items:
        limitations.insert(0, NO_EVIDENCE)
    return {
        "fixes": fix_items,
        "files": file_items,
        "counts": {
            "fix_commits": len(fix_items),
            "bug_links": len(links),
            "files": len(per_file),
        },
        "limitations": limitations,
    }


# ---------------------------------------------------------------- timeline


Bucket = Literal["week", "month"]
MAX_BUCKETS = {"week": 104, "month": 36}


def bucket_start(value: datetime, bucket: Bucket) -> date:
    day = _as_utc(value).astimezone(UTC).date()
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_bucket(value: date, bucket: Bucket) -> date:
    if bucket == "week":
        return value + timedelta(days=7)
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def timeline(facts: SnapshotFacts, bucket: Bucket, *, path: str | None = None) -> dict[str, Any]:
    commit_by_sha = {commit.sha: commit for commit in facts.commits}
    introduced_by_sha: Counter[str] = Counter(
        link.introducing_sha for link in facts.bug_links if path is None or link.path == path
    )
    changes = [
        change
        for change in facts.file_changes
        if (path is None and change.path in facts.component_of) or change.path == path
    ]
    if not changes:
        return {"buckets": [], "overall": [], "components": [], "limitations": [NO_EVIDENCE]}
    starts = sorted({bucket_start(change.authored_at, bucket) for change in changes})
    series: list[date] = []
    cursor = starts[0]
    while cursor <= starts[-1]:
        series.append(cursor)
        cursor = _next_bucket(cursor, bucket)
    series = series[-MAX_BUCKETS[bucket] :]
    first = series[0]

    def empty() -> dict[date, dict[str, Any]]:
        return {
            start: {"commits": set(), "churn": 0, "fixes": set(), "authors": set(), "intro": set()}
            for start in series
        }

    overall = empty()
    per_component: dict[str, dict[date, dict[str, Any]]] = defaultdict(empty)
    for change in changes:
        start = bucket_start(change.authored_at, bucket)
        if start < first:
            continue
        commit = commit_by_sha.get(change.commit_sha)
        targets = [overall]
        component = facts.component_of.get(change.path)
        if path is None and component is not None:
            targets.append(per_component[component])
        for target in targets:
            cell = target[start]
            cell["commits"].add(change.commit_sha)
            cell["churn"] += change.churn
            if commit is not None:
                cell["authors"].add(commit.author_email.lower())
                if is_fix_message(commit.message) and len(commit.parent_shas or []) <= 1:
                    cell["fixes"].add(change.commit_sha)
            if introduced_by_sha.get(change.commit_sha):
                cell["intro"].add(change.commit_sha)

    def points(cells: dict[date, dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "bucket_start": start,
                "commits": len(cell["commits"]),
                "churn": cell["churn"],
                "fix_commits": len(cell["fixes"]),
                "authors": len(cell["authors"]),
                "bug_introducing_commits": len(cell["intro"]),
            }
            for start, cell in sorted(cells.items())
        ]

    roles = architecture_facts(facts).roles if path is None else {}
    return {
        "buckets": series,
        "overall": points(overall),
        "components": [
            {"name": name, "role": roles.get(name, ("unknown", ""))[0], "points": points(cells)}
            for name, cells in sorted(per_component.items())
        ],
        "limitations": [
            "Covers only the commits mined for this snapshot (bounded, possibly shallow "
            "history), not the full repository history.",
            "Churn per file is the commit's total churn divided evenly across its files.",
            f"Fix commits use a keyword rule: {FIX_RULE}",
            f"Bug-introducing commits come from SZZ-lite ({SZZ_VERSION}) candidates, not proof.",
        ],
    }
