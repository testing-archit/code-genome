"""Hidden module discovery from four signals, with a transparent algorithm comparison.

Every analysed file that has at least one import or co-change edge gets a representation
made of four blocks, each reduced to at most 32 dimensions and L2-normalised so the
cosine similarity of two concatenated rows is the average of the per-signal cosines:

* **dependency**: the file's row in the undirected import adjacency (plus itself), so
  files sharing import neighbours are similar;
* **co-change**: the file's row in the co-change count matrix (commits touching 2-20
  analysed files), log-scaled;
* **semantic**: LSA (TF-IDF over identifier-aware path tokens and declared symbol names,
  then truncated SVD). This is latent semantic analysis, not a neural embedding;
* **developer**: TF-IDF weighted commits per author on the file.

Candidates: K-Means (k chosen by silhouette), DBSCAN (eps from the k-distance knee),
Ward agglomerative clustering (k chosen by silhouette), and Louvain community detection on
the weighted import + co-change graph. Each is scored with silhouette and Davies-Bouldin
in the four-signal space, modularity on the import + co-change graph, and a held-out
check: the representation is rebuilt from history before a 70% time cut-off and we
measure how many file pairs changed together *after* the cut-off fall in one cluster,
relative to chance (lift). The directory grouping a reader would guess is the baseline.

The champion has the best mean rank across those four scores; ties go to the more
established method. Clusters are inferred structure, not declared architecture.
"""

import math
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any

import networkx as nx
import numpy as np
from networkx.algorithms.community import louvain_communities, modularity
from scipy import sparse
from scipy.cluster.hierarchy import ClusterWarning, fcluster, linkage
from sklearn.cluster import DBSCAN, KMeans
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.feature_extraction.text import TfidfTransformer, TfidfVectorizer
from sklearn.metrics import davies_bouldin_score, silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize

from .records import ChangeRecord, ImportRecord, bulk_commit_shas
from .text import tokenize

MODEL_VERSION = "modules-multisignal@2"
RANDOM_STATE = 7
BLOCK_DIMENSIONS = 32
MAX_CLUSTER_FILES = 3000
MAX_PROJECTION_POINTS = 1500
MAX_COCHANGE_FILES = 20
HELDOUT_FRACTION = 0.3
MAX_UNASSIGNED_SHARE = 0.2
SIGNALS: tuple[str, ...] = ("dependency", "co_change", "semantic", "developer")
SIGNAL_LABELS: dict[str, str] = {
    "dependency": "shared import neighbours",
    "co_change": "co-change history",
    "semantic": "LSA over path tokens and symbol names",
    "developer": "developer overlap",
}
ABLATIONS: dict[str, tuple[str, ...]] = {
    "structure_only": ("dependency",),
    "structure_cochange": ("dependency", "co_change"),
    "all_signals": SIGNALS,
}
ALGORITHMS: tuple[str, ...] = ("louvain", "agglomerative", "kmeans", "dbscan")
# Tie-break: prefer the method readers know best when mean ranks are equal.
_PREFERENCE = {"louvain": 3, "agglomerative": 2, "kmeans": 1, "dbscan": 0}
SELECTION_METRICS: tuple[tuple[str, bool], ...] = (
    ("silhouette", True),
    ("davies_bouldin", False),
    ("modularity", True),
    ("heldout_lift", True),
)


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
    projection: list[dict[str, object]] = field(default_factory=list)
    projection_method: str | None = None


def _directory_key(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "(root)"


def _commit_files(
    changes: list[ChangeRecord], known: set[str], start: datetime | None, end: datetime | None
) -> dict[str, set[str]]:
    by_commit: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        if change.path not in known:
            continue
        if start is not None and change.authored_at < start:
            continue
        if end is not None and change.authored_at >= end:
            continue
        by_commit[change.commit_sha].add(change.path)
    return by_commit


def _graph(
    paths: list[str], by_commit: dict[str, set[str]], imports: list[ImportRecord]
) -> nx.Graph:
    known = set(paths)
    graph = nx.Graph()
    graph.add_nodes_from(paths)
    for edge in imports:
        if edge.source in known and edge.target in known and edge.source != edge.target:
            weight = graph.get_edge_data(edge.source, edge.target, {"weight": 0.0})["weight"]
            graph.add_edge(edge.source, edge.target, weight=weight + 1.0)
    for files in by_commit.values():
        if 1 < len(files) <= MAX_COCHANGE_FILES:
            for left, right in combinations(sorted(files), 2):
                weight = graph.get_edge_data(left, right, {"weight": 0.0})["weight"]
                graph.add_edge(left, right, weight=min(weight + 0.5, 6.0))
    return graph


def _reduce(matrix: Any, dims: int = BLOCK_DIMENSIONS) -> np.ndarray:
    """Dense, row-normalised block of at most ``dims`` columns. Zero rows stay zero."""
    matrix = sparse.csr_matrix(matrix, dtype=float)
    rows, columns = matrix.shape
    if columns <= dims or rows <= 2:
        dense = matrix.toarray()
    else:
        rank = min(dims, rows - 1, columns - 1)
        dense = TruncatedSVD(n_components=rank, random_state=RANDOM_STATE).fit_transform(matrix)
    result: np.ndarray = normalize(dense)
    return result


def _with_self(matrix: Any) -> Any:
    """Add a self-entry to rows that have any edge so direct neighbours look alike."""
    matrix = sparse.csr_matrix(matrix, dtype=float)
    active = np.asarray(matrix.sum(axis=1)).ravel() > 0
    row_max = np.asarray(matrix.max(axis=1).todense()).ravel()
    return matrix + sparse.diags(np.where(active, np.maximum(row_max, 1.0), 0.0))


def signal_blocks(
    nodes: list[str],
    by_commit: dict[str, set[str]],
    imports: list[ImportRecord],
    symbols: dict[str, tuple[str, ...]],
    commit_authors: dict[str, str],
) -> dict[str, np.ndarray]:
    """Per-file representation for each signal, aligned with ``nodes``."""
    index = {path: position for position, path in enumerate(nodes)}
    n = len(nodes)

    dependency = sparse.lil_matrix((n, n))
    for edge in imports:
        left, right = index.get(edge.source), index.get(edge.target)
        if left is not None and right is not None and left != right:
            dependency[left, right] = 1.0
            dependency[right, left] = 1.0

    cochange = sparse.lil_matrix((n, n))
    for files in by_commit.values():
        members = sorted(index[path] for path in files if path in index)
        if 1 < len(members) <= MAX_COCHANGE_FILES:
            for left, right in combinations(members, 2):
                cochange[left, right] += 1.0
                cochange[right, left] += 1.0
    cochange_csr = sparse.csr_matrix(cochange)
    cochange_csr.data = np.log1p(cochange_csr.data)

    texts = [f"{path.rsplit('.', 1)[0]} {' '.join(symbols.get(path, ())[:60])}" for path in nodes]
    try:
        semantic = TfidfVectorizer(analyzer=tokenize, sublinear_tf=True).fit_transform(texts)
    except ValueError:  # empty vocabulary
        semantic = sparse.csr_matrix((n, 1))

    authors = sorted(
        {commit_authors[sha] for sha, files in by_commit.items() if sha in commit_authors}
    )
    author_index = {name: position for position, name in enumerate(authors)}
    developer = sparse.lil_matrix((n, max(1, len(authors))))
    for sha, files in by_commit.items():
        name = commit_authors.get(sha)
        if name is None:
            continue
        for path in files:
            if path in index:
                developer[index[path], author_index[name]] += 1.0
    developer_csr = sparse.csr_matrix(developer)
    developer_csr.data = np.log1p(developer_csr.data)
    if developer_csr.nnz:
        developer_csr = TfidfTransformer(sublinear_tf=False).fit_transform(developer_csr)

    return {
        "dependency": _reduce(_with_self(dependency)),
        "co_change": _reduce(_with_self(cochange_csr)),
        "semantic": _reduce(semantic),
        "developer": _reduce(developer_csr),
    }


def combine(blocks: dict[str, np.ndarray], signals: tuple[str, ...]) -> np.ndarray:
    """Concatenate blocks with equal weight: cosine of rows = mean of signal cosines."""
    scale = 1 / math.sqrt(len(signals))
    combined: np.ndarray = np.hstack([blocks[name] * scale for name in signals])
    return combined


def _candidate_ks(n: int) -> list[int]:
    upper = min(n - 1, max(3, int(math.sqrt(n)) + 3), 24)
    return list(range(2, upper + 1))


def _silhouette(x: np.ndarray, labels: np.ndarray) -> float | None:
    mask = labels >= 0
    clusters = len(set(labels[mask].tolist()))
    if clusters < 2 or clusters >= int(mask.sum()):
        return None
    return float(
        silhouette_score(
            x[mask],
            labels[mask],
            sample_size=min(2000, int(mask.sum())),
            random_state=RANDOM_STATE,
        )
    )


def _best_by_silhouette(
    x: np.ndarray, fit: Callable[[int], np.ndarray]
) -> tuple[np.ndarray, dict[str, object]]:
    best: tuple[float, int, np.ndarray] | None = None
    tried = []
    # Identical rows (e.g. no signal at all) cannot support more clusters than distinct points.
    distinct = len(np.unique(np.round(x, 8), axis=0))
    for k in [k for k in _candidate_ks(len(x)) if k < distinct]:
        labels = fit(k)
        score = _silhouette(x, labels)
        tried.append([k, round(score, 4) if score is not None else None])
        if score is not None and (best is None or score > best[0]):
            best = (score, k, labels)
    if best is None:
        return np.zeros(len(x), dtype=int), {"k": 1, "silhouette_by_k": tried}
    return best[2], {"k": best[1], "silhouette_by_k": tried}


def _kmeans(x: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    def fit(k: int) -> np.ndarray:
        labels: np.ndarray = KMeans(n_clusters=k, n_init=4, random_state=RANDOM_STATE).fit_predict(
            x
        )
        return labels

    return _best_by_silhouette(x, fit)


def _agglomerative(x: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    with warnings.catch_warnings():
        # ``x`` holds observation vectors; a square block can trip scipy's distance-matrix guess.
        warnings.simplefilter("ignore", ClusterWarning)
        tree = linkage(x, method="ward")

    def fit(k: int) -> np.ndarray:
        labels: np.ndarray = fcluster(tree, t=k, criterion="maxclust") - 1
        return labels

    labels, parameters = _best_by_silhouette(x, fit)
    parameters["linkage"] = "ward"
    return labels, parameters


def _dbscan(x: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    min_samples = 4 if len(x) >= 40 else 3
    neighbours = NearestNeighbors(n_neighbors=min(min_samples, len(x))).fit(x)
    distances = np.sort(neighbours.kneighbors(x)[0][:, -1])
    # Knee of the sorted k-distance curve: the point farthest from the chord joining its ends.
    points = np.column_stack([np.linspace(0, 1, len(distances)), distances])
    start, end = points[0], points[-1]
    chord = end - start
    norm = float(np.linalg.norm(chord)) or 1.0
    offsets = np.abs(chord[0] * (points[:, 1] - start[1]) - chord[1] * (points[:, 0] - start[0]))
    eps = float(distances[int(np.argmax(offsets / norm))])
    if eps <= 1e-6:
        positive = distances[distances > 1e-6]
        eps = float(np.median(positive)) if len(positive) else 0.5
    labels: np.ndarray = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(x)
    return labels, {"eps": round(eps, 4), "min_samples": min_samples, "eps_rule": "k-distance knee"}


def _louvain(graph: nx.Graph, nodes: list[str]) -> tuple[np.ndarray, dict[str, object]]:
    communities = louvain_communities(graph, weight="weight", resolution=1.0, seed=RANDOM_STATE)
    label = {path: number for number, group in enumerate(communities) for path in group}
    return np.asarray([label.get(path, -1) for path in nodes], dtype=int), {"resolution": 1.0}


def _run(
    name: str, x: np.ndarray, graph: nx.Graph, nodes: list[str]
) -> tuple[np.ndarray, dict[str, object]]:
    if name == "louvain":
        return _louvain(graph, nodes)
    if name == "agglomerative":
        return _agglomerative(x)
    if name == "kmeans":
        return _kmeans(x)
    return _dbscan(x)


def _groups(labels: np.ndarray, nodes: list[str]) -> list[set[str]]:
    """Partition for modularity: unassigned (noise) files become singletons."""
    grouped: dict[int, set[str]] = defaultdict(set)
    singles = []
    for path, label in zip(nodes, labels.tolist(), strict=True):
        if label < 0:
            singles.append({path})
        else:
            grouped[label].add(path)
    return list(grouped.values()) + singles


def _heldout(
    labels: np.ndarray, nodes: list[str], pairs: list[tuple[str, str]]
) -> dict[str, float | None]:
    if not pairs:
        return {"heldout_within_share": None, "heldout_lift": None}
    label = dict(zip(nodes, labels.tolist(), strict=True))
    within = sum(1 for left, right in pairs if label[left] >= 0 and label[left] == label[right])
    sizes = Counter(value for value in labels.tolist() if value >= 0)
    chance = sum((size / len(nodes)) ** 2 for size in sizes.values())
    share = within / len(pairs)
    return {
        "heldout_within_share": round(share, 4),
        "heldout_lift": round(share / chance, 4) if chance > 0 else None,
    }


def _score(
    labels: np.ndarray,
    x: np.ndarray,
    graph: nx.Graph,
    nodes: list[str],
    heldout_labels: np.ndarray | None,
    heldout_pairs: list[tuple[str, str]],
) -> dict[str, object]:
    mask = labels >= 0
    clusters = len(set(labels[mask].tolist()))
    silhouette = _silhouette(x, labels)
    davies = (
        float(davies_bouldin_score(x[mask], labels[mask]))
        if 2 <= clusters < int(mask.sum())
        else None
    )
    result: dict[str, object] = {
        "clusters": clusters,
        "unassigned_share": round(float(1 - mask.mean()), 4),
        "silhouette": round(silhouette, 4) if silhouette is not None else None,
        "davies_bouldin": round(davies, 4) if davies is not None else None,
        "modularity": round(float(modularity(graph, _groups(labels, nodes), weight="weight")), 4),
    }
    result.update(
        _heldout(heldout_labels, nodes, heldout_pairs)
        if heldout_labels is not None
        else {"heldout_within_share": None, "heldout_lift": None}
    )
    return result


def select_champion(scores: dict[str, dict[str, object]]) -> tuple[str | None, dict[str, float]]:
    """Mean rank over silhouette (high), Davies-Bouldin (low), modularity (high), and held-out
    lift (high). Degenerate partitions (<2 clusters, or more than 20% of files left
    unassigned, which would let DBSCAN score only its easy points) are not eligible.
    Metrics nobody could compute are skipped; a missing value ranks last."""
    eligible = [
        name
        for name, values in scores.items()
        if int(values.get("clusters", 0) or 0) >= 2  # type: ignore[call-overload]
        and float(values.get("unassigned_share", 0.0) or 0.0)  # type: ignore[arg-type]
        <= MAX_UNASSIGNED_SHARE
    ]
    if not eligible:
        return None, {}
    ranks: dict[str, list[float]] = {name: [] for name in eligible}
    for metric, higher in SELECTION_METRICS:
        values = {name: scores[name].get(metric) for name in eligible}
        present = {name: float(v) for name, v in values.items() if isinstance(v, int | float)}
        if not present:
            continue
        ordered = sorted(present.values(), reverse=higher)
        for name in eligible:
            if name in present:
                value = present[name]
                positions = [i + 1 for i, other in enumerate(ordered) if other == value]
                ranks[name].append(sum(positions) / len(positions))
            else:
                ranks[name].append(float(len(eligible)))
    mean_rank = {name: round(float(np.mean(values)), 3) for name, values in ranks.items()}
    champion = min(eligible, key=lambda name: (mean_rank[name], -_PREFERENCE.get(name, 0)))
    return champion, mean_rank


def _name_modules(
    groups: list[set[str]], graph: nx.Graph
) -> list[LearnedModule]:  # TF-IDF style naming
    global_counts: Counter[str] = Counter()
    group_counts = []
    for group in groups:
        counts: Counter[str] = Counter()
        for path in group:
            counts.update(set(tokenize(path.rsplit(".", 1)[0], translate=False, stem=False)))
        group_counts.append(counts)
        global_counts.update(counts)
    modules = []
    for group, counts in zip(groups, group_counts, strict=True):
        ranked = sorted(
            counts,
            key=lambda term: (
                -(counts[term] / len(group)) * (len(groups) / global_counts[term]),
                term,
            ),
        )
        keywords = ranked[:4]
        internal = graph.subgraph(group).size(weight="weight")
        total = sum(weight for _, _, weight in graph.edges(group, data="weight"))
        modules.append(
            LearnedModule(
                name=" / ".join(keywords[:2]) or "module",
                files=sorted(group),
                cohesion=round(float(internal / total) if total else 0.0, 4),
                keywords=keywords,
            )
        )
    return modules


def discover_modules(
    paths: list[str],
    changes: list[ChangeRecord],
    imports: list[ImportRecord],
    symbols: dict[str, tuple[str, ...]] | None = None,
    commit_authors: dict[str, str] | None = None,
) -> ModuleDiscovery:
    symbols = symbols or {}
    commit_authors = commit_authors or {}
    known = set(paths)
    bulk = bulk_commit_shas(changes, len(paths))
    changes = [change for change in changes if change.commit_sha not in bulk]
    by_commit = _commit_files(changes, known, None, None)
    graph = _graph(paths, by_commit, imports)
    connected = [node for node in graph if graph.degree(node) > 0]
    if graph.number_of_edges() < 3 or len(connected) < 4:
        return ModuleDiscovery(
            MODEL_VERSION,
            "insufficient_data",
            "The import and co-change graph is too sparse to cluster.",
        )
    if len(connected) > MAX_CLUSTER_FILES:
        strength = dict(graph.degree(connected, weight="weight"))
        connected = sorted(connected, key=lambda path: (-strength[path], path))[:MAX_CLUSTER_FILES]
    nodes = sorted(connected)
    subgraph = graph.subgraph(nodes)

    # Held-out split: rebuild history-based signals from commits before a time cut-off and
    # check whether later co-changes stay inside one cluster.
    times = sorted({change.authored_at for change in changes if change.path in known})
    cutoff = times[int(len(times) * (1 - HELDOUT_FRACTION))] if len(times) >= 10 else None
    heldout_pairs: list[tuple[str, str]] = []
    early_blocks: dict[str, np.ndarray] | None = None
    early_graph: nx.Graph | None = None
    if cutoff is not None:
        node_set = set(nodes)
        later = _commit_files(changes, node_set, cutoff, None)
        for files in later.values():
            if 1 < len(files) <= MAX_COCHANGE_FILES:
                heldout_pairs.extend(combinations(sorted(files), 2))
        if len(heldout_pairs) >= 5:
            early = _commit_files(changes, node_set, None, cutoff)
            early_blocks = signal_blocks(nodes, early, imports, symbols, commit_authors)
            early_graph = _graph(nodes, early, imports)
        else:
            heldout_pairs = []

    blocks = signal_blocks(nodes, by_commit, imports, symbols, commit_authors)
    x = combine(blocks, SIGNALS)
    x_early = combine(early_blocks, SIGNALS) if early_blocks is not None else None

    labels: dict[str, np.ndarray] = {}
    algorithms: dict[str, dict[str, object]] = {}
    for name in ALGORITHMS:
        fitted, parameters = _run(name, x, subgraph, nodes)
        early_labels = (
            _run(name, x_early, early_graph, nodes)[0]
            if x_early is not None and early_graph is not None
            else None
        )
        labels[name] = fitted
        algorithms[name] = _score(fitted, x, subgraph, nodes, early_labels, heldout_pairs)
        algorithms[name]["parameters"] = parameters

    shared = min(len(path.split("/")) - 1 for path in nodes)
    depth = max(1, shared + 1)
    directory_keys = sorted({_directory_key(node, depth) for node in nodes})
    directory_index = {key: number for number, key in enumerate(directory_keys)}
    directory_labels = np.asarray(
        [directory_index[_directory_key(node, depth)] for node in nodes], dtype=int
    )
    directory = _score(directory_labels, x, subgraph, nodes, directory_labels, heldout_pairs)

    champion, mean_rank = select_champion(algorithms)
    if champion is None:
        return ModuleDiscovery(
            MODEL_VERSION,
            "insufficient_data",
            "No clustering algorithm produced at least two clusters on this repository.",
            metrics={"algorithms": algorithms, "directory_baseline": directory},
        )
    for name, rank in mean_rank.items():
        algorithms[name]["mean_rank"] = rank

    # Signal ablation with the best representation-based algorithm, measured with the same
    # yardsticks (silhouette and Davies-Bouldin in the four-signal space).
    representation_based = [name for name in ("agglomerative", "kmeans") if name in mean_rank]
    ablation_algorithm = (
        min(representation_based, key=lambda name: (mean_rank[name], -_PREFERENCE[name]))
        if representation_based
        else "kmeans"
    )
    ablation: dict[str, dict[str, object]] = {}
    for variant, signals in ABLATIONS.items():
        if variant == "all_signals":
            fitted, early_labels = labels[ablation_algorithm], None
            ablation[variant] = {
                key: algorithms[ablation_algorithm][key]
                for key in algorithms[ablation_algorithm]
                if key not in {"parameters", "mean_rank"}
            }
        else:
            fitted = _run(ablation_algorithm, combine(blocks, signals), subgraph, nodes)[0]
            early_labels = (
                _run(ablation_algorithm, combine(early_blocks, signals), subgraph, nodes)[0]
                if early_blocks is not None
                else None
            )
            ablation[variant] = _score(fitted, x, subgraph, nodes, early_labels, heldout_pairs)
        ablation[variant]["signals"] = list(signals)

    chosen = labels[champion]
    groups = [
        {path for path, label in zip(nodes, chosen.tolist(), strict=True) if label == value}
        for value in sorted(set(chosen.tolist()) - {-1})
    ]
    modules = _name_modules(groups, subgraph)
    order = sorted(range(len(modules)), key=lambda i: (-len(modules[i].files), modules[i].name))
    modules = [modules[i] for i in order]
    module_of = {path: number for number, module in enumerate(modules) for path in module.files}

    strength = dict(subgraph.degree(nodes, weight="weight"))
    shown = sorted(nodes, key=lambda path: (-strength[path], path))[:MAX_PROJECTION_POINTS]
    shown_index = [nodes.index(path) for path in sorted(shown)]
    projection: list[dict[str, object]] = []
    explained: list[float] = []
    if len(shown_index) >= 3:
        pca = PCA(n_components=2, random_state=RANDOM_STATE)
        coordinates = pca.fit_transform(x[shown_index])
        explained = [round(float(value), 4) for value in pca.explained_variance_ratio_]
        projection = [
            {
                "path": nodes[i],
                "x": round(float(point[0]), 4),
                "y": round(float(point[1]), 4),
                "cluster": module_of.get(nodes[i], -1),
            }
            for i, point in zip(shown_index, coordinates, strict=True)
        ]

    champion_scores = algorithms[champion]
    metrics: dict[str, object] = {
        # Keys kept from louvain-import-cochange@1; they now describe the champion.
        "communities": len(modules),
        "clustered_files": len(nodes),
        "isolated_files": len(paths) - len(nodes),
        "modularity_learned": champion_scores["modularity"],
        "modularity_directory_baseline": directory["modularity"],
        "directory_groups": len(directory_keys),
        # Multi-signal comparison.
        "champion": champion,
        "selection_rule": (
            "Lowest mean rank across silhouette (higher is better), Davies-Bouldin (lower), "
            "modularity (higher), and held-out co-change lift (higher); clusterings with "
            "fewer than two clusters or over 20% of files unassigned are not eligible."
        ),
        "algorithms": algorithms,
        "directory_baseline": directory,
        "beats_directory_baseline": {
            metric: (
                None
                if champion_scores.get(metric) is None or directory.get(metric) is None
                else (
                    float(champion_scores[metric]) > float(directory[metric])  # type: ignore[arg-type]
                    if higher
                    else float(champion_scores[metric]) < float(directory[metric])  # type: ignore[arg-type]
                )
            )
            for metric, higher in SELECTION_METRICS
        },
        "ablation": ablation,
        "ablation_algorithm": ablation_algorithm,
        "signals": [{"name": name, "label": SIGNAL_LABELS[name]} for name in SIGNALS],
        "heldout": {
            "cutoff": cutoff.isoformat() if cutoff is not None and heldout_pairs else None,
            "pairs": len(heldout_pairs),
            "note": (
                "History-based signals are rebuilt from commits before the cut-off; the import "
                "graph and symbols come from the analysed snapshot."
            ),
        },
        "unassigned_files": int((chosen < 0).sum()),
        "projection_explained_variance": explained,
    }
    return ModuleDiscovery(
        MODEL_VERSION,
        "trained",
        None,
        metrics=metrics,
        modules=modules,
        projection=projection,
        projection_method="pca" if projection else None,
    )
