"""Answer "how does X work?" by walking the genome graph from X (``code-flow@1``).

The walk follows file-level CALLS and IMPORTS edges downstream for up to three hops, adds the
data stores and external APIs the reached components use, and lists who calls into X. Every
step carries the edge's evidence; inferred edges (static call resolution, data-store detection)
are labelled as such. A model may only rephrase the result.
"""

from collections import deque
from dataclasses import dataclass, field

from .genome import Edge, GenomeGraphBuilder

FLOW_VERSION = "code-flow@1"
MAX_DEPTH = 3
MAX_CHAINS = 5
# Calls describe behaviour more directly than imports, so they are walked first.
_WALK_ORDER = {"CALLS": 0, "IMPORTS": 1}
_RESOURCE_KINDS = {
    "READS_FROM": "reads from",
    "WRITES_TO": "writes to",
    "USES_DATASTORE": "uses data store",
    "CALLS_API": "calls external API",
}


@dataclass(frozen=True)
class FlowStep:
    source: str
    target: str
    kind: str
    inferred: bool
    evidence_ids: tuple[str, ...]


@dataclass
class CodeFlow:
    start: str
    chains: list[list[FlowStep]] = field(default_factory=list)
    callers: list[FlowStep] = field(default_factory=list)
    symbol_calls: list[FlowStep] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.chains or self.callers or self.symbol_calls)


def label(graph: GenomeGraphBuilder, node_id: str) -> str:
    node = graph.nodes.get(node_id)
    if node is None:
        return node_id
    if node.kind == "file":
        return str(node.properties.get("path") or node.label)
    if node.kind in {"function", "class"}:
        return f"{node.label}()" if node.kind == "function" else node.label
    return node.label


def _step(edge: Edge) -> FlowStep:
    return FlowStep(edge.source, edge.target, edge.kind, edge.inferred, tuple(edge.evidence_ids))


def trace_flow(graph: GenomeGraphBuilder, start_file: str) -> CodeFlow:
    """Downstream chains, upstream callers, and in-file symbol calls for ``start_file``."""
    start = f"file:{start_file}"
    flow = CodeFlow(start=start)
    if start not in graph.nodes:
        return flow
    outgoing: dict[str, list[Edge]] = {}
    incoming: dict[str, list[Edge]] = {}
    component_of: dict[str, str] = {}
    resources: dict[str, list[Edge]] = {}
    for edge in graph.edges.values():
        if edge.kind in _WALK_ORDER and edge.source.startswith("file:"):
            if edge.target.startswith("file:"):
                outgoing.setdefault(edge.source, []).append(edge)
                incoming.setdefault(edge.target, []).append(edge)
        elif edge.kind == "BELONGS_TO_MODULE":
            component_of[edge.source] = edge.target
        elif edge.kind in _RESOURCE_KINDS:
            resources.setdefault(edge.source, []).append(edge)
        elif edge.kind == "DECLARES" and edge.source == start:
            flow.declared.append(label(graph, edge.target))
        elif (
            edge.kind == "CALLS"
            and graph.nodes[edge.source].kind in {"function", "class"}
            and graph.nodes[edge.source].properties.get("path") == start_file
        ):
            flow.symbol_calls.append(_step(edge))
    for edges in outgoing.values():
        edges.sort(key=lambda item: (_WALK_ORDER[item.kind], item.inferred, item.target))

    # Breadth-first so each reached file keeps its shortest, call-preferring path.
    parent: dict[str, Edge] = {}
    depth = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if depth[current] >= MAX_DEPTH:
            continue
        for edge in outgoing.get(current, []):
            if edge.target not in depth:
                depth[edge.target] = depth[current] + 1
                parent[edge.target] = edge
                queue.append(edge.target)

    def path_to(node_id: str) -> list[FlowStep]:
        steps: list[FlowStep] = []
        while node_id in parent:
            edge = parent[node_id]
            steps.append(_step(edge))
            node_id = edge.source
        return list(reversed(steps))

    def ends_in_resource(node_id: str) -> list[Edge]:
        component = component_of.get(node_id)
        return resources.get(component, []) if component else []

    reached = [node for node in depth if node != start]
    has_children = {parent[node].source for node in reached}
    leaves = [node for node in reached if node not in has_children]
    # Prefer chains that reach a data store or API, then deeper ones, then call-only ones.
    leaves.sort(
        key=lambda node: (
            not ends_in_resource(node),
            -depth[node],
            any(step.kind != "CALLS" for step in path_to(node)),
            node,
        )
    )
    for leaf in leaves[:MAX_CHAINS]:
        chain = path_to(leaf)
        chain.extend(_step(edge) for edge in ends_in_resource(leaf)[:2])
        flow.chains.append(chain)
    if not flow.chains:
        chain = [_step(edge) for edge in ends_in_resource(start)[:2]]
        if chain:
            flow.chains.append(chain)
    callers = sorted(
        incoming.get(start, []), key=lambda item: (_WALK_ORDER[item.kind], item.source)
    )
    flow.callers = [_step(edge) for edge in callers[:5]]
    flow.symbol_calls = sorted(
        flow.symbol_calls, key=lambda step: (step.inferred, step.source, step.target)
    )[:6]
    # Public names first: private members (#name, _name) say less about what the file offers.
    flow.declared = sorted(
        dict.fromkeys(flow.declared), key=lambda name: (name.startswith(("#", "_")), name)
    )[:8]
    return flow


def verb(step: FlowStep) -> str:
    if step.kind in _RESOURCE_KINDS:
        return _RESOURCE_KINDS[step.kind]
    return "calls" if step.kind == "CALLS" else "imports"
