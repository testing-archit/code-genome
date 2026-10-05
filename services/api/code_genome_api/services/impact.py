"""One-hop change impact from observed imports, repeated co-change, and a learned model.

Every returned item also carries the explainable weighted score (``impact-weighted@1``):
0.35 dependency strength + 0.30 co-change confidence + 0.20 import proximity + 0.15 shared
bug-fix history, with each component in [0, 1] so the UI can show why a file is listed."""

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from code_genome_intelligence import ImpactRelation, rank_impact
from code_genome_ml import (
    IMPACT_WEIGHTED_VERSION,
    ImpactSignalContext,
    TrainingInputs,
    import_centrality,
    keyword_fix_shas,
    predict_impact,
    weighted_impact_score,
)
from code_genome_ml.records import bulk_commit_shas
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CoChangeEdge, GraphEdge, GraphNode, RepositorySnapshot
from ..schemas import ImpactGraphMetricsResponse, ImpactItemResponse, ImpactSignalsResponse
from . import ml


@dataclass(frozen=True)
class _Prepared:
    """Per-snapshot history and graph statistics shared by every impact request."""

    inputs: TrainingInputs
    signals: ImpactSignalContext
    betweenness: dict[str, float]
    pagerank: dict[str, float]
    pagerank_percentile: dict[str, float]


# Published snapshots are immutable, so the key (workspace, snapshot, analyzer version, fix
# set or keyword rule) fully determines the result; a re-analysis or retrained intent model
# changes the key, and a snapshot rebuilt in place gets a new analysis_version.
_CACHE_SIZE = 8
_cache: OrderedDict[tuple[str, str, str, int], _Prepared] = OrderedDict()
_cache_lock = threading.Lock()
_KEYWORD_RULE = -1


def _prepare(db: Session, snapshot: RepositorySnapshot, fix_shas: set[str] | None) -> _Prepared:
    """Load (or reuse) history and graph statistics; ``fix_shas=None`` applies the keyword
    fix rule to the snapshot's own commits."""
    fix_key = _KEYWORD_RULE if fix_shas is None else hash(frozenset(fix_shas))
    key = (snapshot.workspace_id, snapshot.id, snapshot.analysis_version, fix_key)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    inputs = ml.training_inputs(db, snapshot)
    bulk = bulk_commit_shas(inputs.changes, len(inputs.files))
    signals = ImpactSignalContext(
        [change for change in inputs.changes if change.commit_sha not in bulk],
        inputs.imports,
        keyword_fix_shas(inputs.commits) if fix_shas is None else fix_shas,
    )
    betweenness, pagerank = import_centrality(list(inputs.imports))
    ordered = sorted(pagerank, key=lambda path: pagerank[path])
    percentile = {
        path: (index / (len(ordered) - 1) if len(ordered) > 1 else 1.0)
        for index, path in enumerate(ordered)
    }
    prepared = _Prepared(inputs, signals, betweenness, pagerank, percentile)
    with _cache_lock:
        _cache[key] = prepared
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return prepared


def impact_for_paths(
    db: Session, snapshot: RepositorySnapshot, paths: Sequence[str]
) -> tuple[dict[str, list[ImpactItemResponse]], list[str]]:
    """One-hop impact for each selected path, loading the snapshot graph once."""
    selected = set(paths)
    co_changes = list(
        db.scalars(
            select(CoChangeEdge).where(
                CoChangeEdge.snapshot_id == snapshot.id,
                CoChangeEdge.workspace_id == snapshot.workspace_id,
                CoChangeEdge.left_path.in_(selected) | CoChangeEdge.right_path.in_(selected),
            )
        )
    )
    nodes = {
        item.id: item
        for item in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
            )
        )
    }
    imports: list[tuple[str, str, float, str]] = []
    for edge in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == snapshot.workspace_id,
            GraphEdge.type == "IMPORTS",
        )
    ):
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source is None or target is None:
            continue
        if source.natural_key in selected or target.natural_key in selected:
            imports.append(
                (source.natural_key, target.natural_key, edge.confidence, edge.provenance_id)
            )
    limitations = [
        "Impact is a one-hop traversal of observed imports and repeated co-change.",
        "Absence from this result does not establish absence of runtime impact.",
    ]
    link_model = ml.trained_result(db, snapshot, "change_impact")
    intent = ml.trained_result(db, snapshot, "commit_intent")
    if intent is not None:
        fix_shas = {
            str(item.get("sha"))
            for item in intent.get("predictions", [])
            if isinstance(item, dict) and item.get("intent") == "fix"
        }
        fix_source = "commits the intent model classified as fixes"
        prepared = _prepare(db, snapshot, fix_shas)
    else:
        fix_source = "commits whose message mentions a fix (keyword rule; intent model untrained)"
        prepared = _prepare(db, snapshot, None)
    inputs = prepared.inputs
    signal_context = prepared.signals
    limitations.append(
        "Graph metrics (PageRank, betweenness) describe where the impacted file sits in the "
        "import graph. They are context for the ranking and are not part of the weighted score."
    )
    limitations.append(
        f"Weighted score ({IMPACT_WEIGHTED_VERSION}) = 0.35 dependency strength + 0.30 "
        "co-change confidence + 0.20 import proximity + 0.15 shared bug-fix history; bug "
        f"fixes are {fix_source}. The weights are fixed by the spec, not learned."
    )
    if link_model is not None:
        limitations.append(
            f"Model-predicted items come from {link_model['model_version']}, trained on this "
            "repository's co-change history; they are inferred and carry no direct evidence."
        )
    results: dict[str, list[ImpactItemResponse]] = {}
    for path in dict.fromkeys(paths):
        relations = [
            ImpactRelation(
                item.left_path,
                item.right_path,
                "co_change",
                item.confidence,
                tuple(f"commit:{sha}" for sha in item.evidence_shas),
            )
            for item in co_changes
            if path in (item.left_path, item.right_path)
        ]
        relations.extend(
            ImpactRelation(source, target, "imports", confidence, (f"evidence:{provenance}",))
            for source, target, confidence, provenance in imports
            if path in (source, target)
        )
        impacted = {
            item.path: ImpactItemResponse(
                path=item.path,
                score=item.score,
                reasons=list(item.reasons),
                evidence_ids=list(item.evidence_ids),
            )
            for item in rank_impact(path, tuple(relations))
        }
        if link_model is not None:
            predictions = predict_impact(
                link_model,
                path,
                inputs.changes,
                inputs.imports,
                [record.path for record in inputs.files],
            )
            for prediction in predictions:
                reason = f"model predicts co-change p={prediction.probability:.2f} (inferred" + (
                    f"; {', '.join(prediction.reasons)})" if prediction.reasons else ")"
                )
                existing = impacted.get(prediction.path)
                if existing:
                    existing.reasons.append(reason)
                    existing.score = round(max(existing.score, prediction.probability), 4)
                elif prediction.probability >= 0.2:
                    impacted[prediction.path] = ImpactItemResponse(
                        path=prediction.path,
                        score=prediction.probability,
                        reasons=[reason],
                        evidence_ids=[],
                    )
        for item in impacted.values():
            signals = signal_context.signals(path, item.path)
            item.signals = ImpactSignalsResponse(**signals)
            item.weighted_score = weighted_impact_score(signals)
            if item.path in prepared.pagerank:
                item.graph_metrics = ImpactGraphMetricsResponse(
                    pagerank=round(prepared.pagerank[item.path], 6),
                    pagerank_percentile=round(prepared.pagerank_percentile[item.path], 4),
                    betweenness=round(min(1.0, prepared.betweenness.get(item.path, 0.0)), 6),
                )
        results[path] = sorted(impacted.values(), key=lambda item: (-item.score, item.path))
    return results, limitations
