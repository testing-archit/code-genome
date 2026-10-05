"use client";

import type { GenomeEdge, GenomeEdgeKind, GenomeNode, GenomeNodeKind } from "@code-genome/contracts";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useMemo, useRef, useState } from "react";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { forceLayout } from "../../../../lib/force-layout";
import { useResource } from "../../../../lib/use-resource";

const NO_EVIDENCE = "No supporting evidence was identified in the selected scope.";

const kinds: Array<{ kind: GenomeNodeKind; label: string; color: string }> = [
  { kind: "component", label: "Components", color: "var(--chart-baseline)" },
  { kind: "file", label: "Files", color: "var(--hema)" },
  { kind: "function", label: "Functions", color: "var(--eosin)" },
  { kind: "class", label: "Classes", color: "var(--eosin)" },
  { kind: "developer", label: "Developers", color: "var(--ok)" },
  { kind: "commit", label: "Commits", color: "var(--fixative-2)" },
  { kind: "external", label: "Packages", color: "var(--fixative)" },
  { kind: "datastore", label: "Data stores", color: "var(--warn)" },
  { kind: "external_api", label: "External APIs", color: "var(--chart-challenger)" },
];
const kindColor = new Map(kinds.map((item) => [item.kind, item.color]));
const kindLabel = new Map(kinds.map((item) => [item.kind, item.label]));

const edgeLabels: Record<GenomeEdgeKind, string> = {
  IMPORTS: "imports",
  CALLS: "calls",
  DECLARES: "declares",
  DEPENDS_ON: "depends on",
  BELONGS_TO_MODULE: "belongs to",
  CO_CHANGED_WITH: "co-changed with",
  MODIFIED_BY: "modified by",
  AUTHORED_BY: "authored by",
  OWNED_BY: "mostly changed by",
  SEMANTICALLY_RELATED_TO: "similar to",
  INTRODUCED_BUG: "introduced bug fixed in",
  FIXED_BY: "fixed by",
  READS_FROM: "reads from",
  WRITES_TO: "writes to",
  USES_DATASTORE: "uses data store",
  CALLS_API: "calls API",
};

export default function GenomePage() {
  return (
    <RequiresSnapshot what="The genome graph">
      <Suspense fallback={<Panel><Loading rows={6} /></Panel>}>
        <GenomeView />
      </Suspense>
    </RequiresSnapshot>
  );
}

function GenomeView() {
  const { repository, published, inventory } = useRepo();
  const router = useRouter();
  const params = useSearchParams();
  const focus = params.get("focus");
  const sha = published?.snapshot_sha ?? "";
  const genome = useResource(`${repository.id}:genome:${sha}:${focus ?? ""}`, () => api.getGenome(repository.id, focus));
  const [hiddenKinds, setHiddenKinds] = useState<Set<GenomeNodeKind>>(() => new Set(focus ? [] : ["commit", "developer"]));
  const [hiddenEdges, setHiddenEdges] = useState<Set<GenomeEdgeKind>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState(focus ?? "");
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const setFocus = (value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set("focus", value);
    else next.delete("focus");
    setSelected(null);
    setView({ x: 0, y: 0, k: 1 });
    router.replace(`/r/${repository.id}/genome${next.size ? `?${next}` : ""}`);
  };

  const visible = useMemo(() => {
    if (!genome.data) return null;
    const nodes = genome.data.nodes.filter((node) => !hiddenKinds.has(node.kind) || node.id === genome.data?.focus_node_id);
    const ids = new Set(nodes.map((node) => node.id));
    const edges = genome.data.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target) && !hiddenEdges.has(edge.kind));
    const positions = forceLayout(nodes.map((node) => node.id), edges, 52);
    const degree = new Map<string, number>();
    for (const edge of edges) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    }
    return { nodes, edges, positions, degree, byId: new Map(genome.data.nodes.map((node) => [node.id, node])) };
  }, [genome.data, hiddenKinds, hiddenEdges]);

  const neighbours = useMemo(() => {
    if (!visible || !selected) return null;
    const set = new Set([selected]);
    for (const edge of visible.edges) {
      if (edge.source === selected) set.add(edge.target);
      if (edge.target === selected) set.add(edge.source);
    }
    return set;
  }, [visible, selected]);

  const suggestions = useMemo(() => (inventory.data?.files ?? []).filter((file) => file.analyzed).map((file) => file.path).slice(0, 2000), [inventory.data]);

  function submitFocus(event: FormEvent) {
    event.preventDefault();
    setFocus(draft.trim() || null);
  }

  const toggle = <T,>(set: Set<T>, value: T) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  const focusForm = (
    <form className="row" onSubmit={submitFocus} style={{ gap: 8, flexWrap: "wrap" }}>
      <label className="sr-only" htmlFor="genome-focus">Focus on a file or component</label>
      <input
        className="input"
        id="genome-focus"
        list="genome-focus-options"
        onChange={(event) => setDraft(event.target.value)}
        placeholder="Focus on a file path or component name"
        style={{ minWidth: 260, flex: 1 }}
        value={draft}
      />
      <datalist id="genome-focus-options">
        {suggestions.map((path) => <option key={path} value={path} />)}
      </datalist>
      <button className="button button-primary" type="submit">Focus</button>
      {focus && <button className="button button-secondary" onClick={() => { setDraft(""); setFocus(null); }} type="button">Show whole genome</button>}
    </form>
  );

  if (genome.error) {
    return (
      <>
        <Panel title="Software genome">{focusForm}</Panel>
        <Notice tone="error" title={focus ? "That focus could not be loaded" : "The genome could not be loaded"}>{genome.error}</Notice>
      </>
    );
  }
  if (!genome.data || !visible) return <Panel><Loading rows={6} /></Panel>;

  const data = genome.data;
  const selectedNode = selected ? visible.byId.get(selected) ?? null : null;
  const selectedEdges = selectedNode ? visible.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id) : [];
  const radius = (node: GenomeNode) => {
    const base = node.kind === "component" ? 9 : node.kind === "file" ? 5 : 4;
    return base + Math.min(8, Math.sqrt(visible.degree.get(node.id) ?? 0) * 1.3);
  };
  const edgeKinds = Object.entries(data.edge_counts) as Array<[GenomeEdgeKind, number]>;

  return (
    <>
      <Panel
        title="Software genome"
        description={
          focus
            ? `Two-step neighbourhood of ${focus} in snapshot ${data.scope.snapshot_sha.slice(0, 8)}.`
            : `Files, components, symbols, people, commits, packages, data stores and APIs in snapshot ${data.scope.snapshot_sha.slice(0, 8)}. Showing ${data.nodes.length} of ${data.total_nodes} nodes.`
        }
      >
        <div style={{ display: "grid", gap: 12 }}>
          {focusForm}
          <div className="chip-row" role="group" aria-label="Show node kinds">
            {kinds.filter((item) => data.node_counts[item.kind]).map((item) => (
              <button aria-pressed={!hiddenKinds.has(item.kind)} className="suggestion kind-filter" key={item.kind} onClick={() => setHiddenKinds((set) => toggle(set, item.kind))} type="button">
                <span aria-hidden="true" className="legend-dot" style={{ background: item.color }} />
                {item.label} ({data.node_counts[item.kind]})
              </button>
            ))}
          </div>
        </div>
      </Panel>

      {data.nodes.length === 0 ? (
        <Panel><Empty title="Nothing to show">{NO_EVIDENCE}</Empty></Panel>
      ) : (
        <div className="split split-side">
          <div className="graph-canvas">
            <svg
              aria-label={`Genome graph with ${visible.nodes.length} nodes and ${visible.edges.length} relationships. Use the panel to browse connections.`}
              onPointerDown={(event) => {
                (event.target as Element).setPointerCapture?.(event.pointerId);
                drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
              }}
              onPointerMove={(event) => {
                if (!drag.current) return;
                const start = drag.current;
                setView((current) => ({ ...current, x: start.vx + event.clientX - start.x, y: start.vy + event.clientY - start.y }));
              }}
              onPointerUp={() => {
                drag.current = null;
              }}
              onWheel={(event) => {
                const factor = event.deltaY > 0 ? 0.9 : 1.1;
                setView((current) => ({ ...current, k: Math.max(0.2, Math.min(5, current.k * factor)) }));
              }}
              role="img"
              viewBox="-500 -350 1000 700"
            >
              <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
                {visible.edges.map((edge) => {
                  const source = visible.positions.get(edge.source);
                  const target = visible.positions.get(edge.target);
                  if (!source || !target) return null;
                  const active = selected !== null && (edge.source === selected || edge.target === selected);
                  return (
                    <line
                      className="graph-edge"
                      data-active={active}
                      data-dim={Boolean(selected && !active)}
                      data-inferred={edge.inferred}
                      key={edge.id}
                      x1={source.x}
                      x2={target.x}
                      y1={source.y}
                      y2={target.y}
                    >
                      <title>{`${edge.kind}${edge.inferred ? " (inferred)" : ""}`}</title>
                    </line>
                  );
                })}
                {visible.nodes.map((node) => {
                  const position = visible.positions.get(node.id);
                  if (!position) return null;
                  const dim = neighbours !== null && !neighbours.has(node.id);
                  const isFocus = node.id === data.focus_node_id;
                  const showLabel = node.id === selected || isFocus || node.kind === "component" || (neighbours?.has(node.id) ?? false) || (visible.degree.get(node.id) ?? 0) > 10 || view.k > 1.8;
                  return (
                    <g
                      aria-label={`${kindLabel.get(node.kind) ?? node.kind}: ${node.label}`}
                      className="graph-node"
                      data-active={node.id === selected || isFocus}
                      data-dim={dim}
                      key={node.id}
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelected(node.id === selected ? null : node.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelected(node.id === selected ? null : node.id);
                        }
                      }}
                      onPointerDown={(event) => event.stopPropagation()}
                      role="button"
                      tabIndex={0}
                      transform={`translate(${position.x} ${position.y})`}
                    >
                      <circle fill={kindColor.get(node.kind) ?? "var(--fixative)"} r={radius(node)} strokeDasharray={node.inferred ? "2 2" : undefined}>
                        <title>{node.label}</title>
                      </circle>
                      {showLabel && <text x={radius(node) + 3} y={3}>{shortLabel(node)}</text>}
                    </g>
                  );
                })}
              </g>
            </svg>
            <div className="graph-zoom">
              <button aria-label="Zoom in" className="icon-button" onClick={() => setView((v) => ({ ...v, k: Math.min(5, v.k * 1.25) }))} type="button">+</button>
              <button aria-label="Zoom out" className="icon-button" onClick={() => setView((v) => ({ ...v, k: Math.max(0.2, v.k / 1.25) }))} type="button">−</button>
              <button aria-label="Reset view" className="icon-button" onClick={() => setView({ x: 0, y: 0, k: 1 })} type="button">⤾</button>
            </div>
          </div>

          <Panel
            title={selectedNode ? shortLabel(selectedNode) : "Relationships"}
            description={selectedNode ? <code style={{ overflowWrap: "anywhere" }}>{selectedNode.label}</code> : `${visible.nodes.length} nodes and ${visible.edges.length} relationships shown.`}
          >
            {selectedNode ? (
              <NodeDetails
                edges={selectedEdges}
                node={selectedNode}
                nodeById={visible.byId}
                onFocus={(value) => {
                  setDraft(value);
                  setFocus(value);
                }}
                onSelect={setSelected}
              />
            ) : (
              <div style={{ display: "grid", gap: 14 }}>
                <p className="muted small">Select a node to see its relationships and the evidence behind each one. Drag to pan, scroll to zoom; nodes are reachable with Tab.</p>
                <div>
                  <h3 style={{ marginBottom: 6 }}>Relationship types</h3>
                  <div className="list">
                    {edgeKinds.map(([kind, count]) => (
                      <label className="list-row" key={kind} style={{ padding: "4px 0" }}>
                        <input checked={!hiddenEdges.has(kind)} onChange={() => setHiddenEdges((set) => toggle(set, kind))} type="checkbox" />
                        <span className="grow">{edgeLabels[kind] ?? kind}</span>
                        <span className="muted small">{count}</span>
                      </label>
                    ))}
                  </div>
                  <p className="muted small" style={{ marginTop: 8 }}>
                    <svg aria-hidden="true" height="8" width="28"><line className="graph-edge" data-inferred="true" x1="0" x2="28" y1="4" y2="4" /></svg>{" "}
                    Dashed lines are inferred (candidate) relationships, such as static call resolution, name similarity, SZZ bug links and data-store detection.
                  </p>
                </div>
                <div className="muted small" style={{ display: "grid", gap: 6 }}>
                  {data.limitations.map((item) => <p key={item}>{item}</p>)}
                  <p>Sources: {data.scope.sources.join("; ")}. Analysis {data.scope.analysis_version}.</p>
                </div>
              </div>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}

function NodeDetails({
  node,
  edges,
  nodeById,
  onSelect,
  onFocus,
}: {
  node: GenomeNode;
  edges: GenomeEdge[];
  nodeById: Map<string, GenomeNode>;
  onSelect: (id: string) => void;
  onFocus: (value: string) => void;
}) {
  const focusValue = node.kind === "file" || node.kind === "component" ? node.id.slice(node.id.indexOf(":") + 1) : null;
  const properties = Object.entries(node.properties).filter(([, value]) => value !== null && value !== "" && (typeof value !== "object" || Array.isArray(value)));
  const grouped = new Map<GenomeEdgeKind, GenomeEdge[]>();
  edges.forEach((edge) => grouped.set(edge.kind, [...(grouped.get(edge.kind) ?? []), edge]));
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="chip-row">
        <span className="badge badge-hema">{kindLabel.get(node.kind) ?? node.kind}</span>
        {node.inferred && <span className="badge badge-warn">inferred</span>}
        <span className="badge">{edges.length} relationships shown</span>
      </div>
      {properties.length > 0 && (
        <dl className="kv">
          {properties.slice(0, 10).map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd style={{ overflowWrap: "anywhere" }}>{Array.isArray(value) ? value.slice(0, 6).join(", ") : String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {focusValue && <button className="button button-secondary" onClick={() => onFocus(focusValue)} type="button">Focus on this {node.kind}</button>}
      {[...grouped.entries()].map(([kind, items]) => (
        <div key={kind}>
          <h3 style={{ marginBottom: 6 }}>{edgeLabels[kind] ?? kind} ({items.length})</h3>
          <div style={{ display: "grid", gap: 8 }}>
            {items.slice(0, 8).map((edge) => {
              const otherId = edge.source === node.id ? edge.target : edge.source;
              const other = nodeById.get(otherId);
              return (
                <div key={edge.id} style={{ display: "grid", gap: 4 }}>
                  <button className="list-row" onClick={() => onSelect(otherId)} style={{ padding: "2px 0" }} type="button">
                    <span className="muted small">{edge.source === node.id ? "→" : "←"}</span>
                    <code className="truncate grow">{other ? other.label : otherId}</code>
                    {edge.inferred && <span className="badge badge-warn" title={`Confidence ${Math.round(edge.confidence * 100)}%`}>inferred {Math.round(edge.confidence * 100)}%</span>}
                  </button>
                  {edge.evidence_ids.length > 0 ? <EvidenceChips ids={edge.evidence_ids} limit={3} /> : <p className="muted small">{NO_EVIDENCE}</p>}
                </div>
              );
            })}
            {items.length > 8 && <p className="muted small">+{items.length - 8} more</p>}
          </div>
        </div>
      ))}
      {node.evidence_ids.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 6 }}>Node evidence</h3>
          <EvidenceChips ids={node.evidence_ids} limit={4} />
        </div>
      )}
    </div>
  );
}

function shortLabel(node: GenomeNode): string {
  if (node.kind === "file") return node.label.split("/").slice(-2).join("/");
  if (node.kind === "commit") return node.label.slice(0, 48);
  return node.label.length > 40 ? `${node.label.slice(0, 39)}…` : node.label;
}
