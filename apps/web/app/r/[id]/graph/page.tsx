"use client";

import type { GraphProjection } from "@code-genome/contracts";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel, SearchField } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { moduleColor, nodeTitle } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

type SimNode = { id: string; key: string; kind: string; x: number; y: number; vx: number; vy: number; degree: number; module: number; evidence: string[] };
type SimEdge = { id: string; source: string; target: string; type: string };

const MAX_NODES = 450;

/* A compact force layout: repulsion, spring edges, and gentle centering. Deterministic seed. */
function layout(graph: GraphProjection, showSymbols: boolean, showPackages: boolean, moduleOf: (key: string) => number) {
  const allowed = new Set(["FILE", ...(showSymbols ? ["SYMBOL"] : []), ...(showPackages ? ["MODULE"] : [])]);
  const candidates = graph.nodes.filter((node) => allowed.has(node.kind));
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    degree.set(edge.from_node, (degree.get(edge.from_node) ?? 0) + 1);
    degree.set(edge.to_node, (degree.get(edge.to_node) ?? 0) + 1);
  }
  candidates.sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0));
  const kept = candidates.slice(0, MAX_NODES);
  const ids = new Set(kept.map((node) => node.id));
  const edges: SimEdge[] = graph.edges
    .filter((edge) => ids.has(edge.from_node) && ids.has(edge.to_node) && edge.from_node !== edge.to_node)
    .filter((edge) => edge.type === "IMPORTS" || showSymbols)
    .map((edge) => ({ id: edge.id, source: edge.from_node, target: edge.to_node, type: edge.type }));
  const nodes: SimNode[] = kept.map((node, index) => {
    const angle = index * 2.399963;
    const radius = 12 * Math.sqrt(index + 1);
    return {
      id: node.id,
      key: node.natural_key,
      kind: node.kind,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      degree: degree.get(node.id) ?? 0,
      module: node.kind === "FILE" ? moduleOf(node.natural_key) : -1,
      evidence: node.evidence_ids,
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const iterations = nodes.length > 250 ? 180 : 260;
  for (let step = 0; step < iterations; step++) {
    const alpha = 1 - step / iterations;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distance = dx * dx + dy * dy;
        if (distance === 0) {
          dx = 0.1;
          dy = 0.1;
          distance = 0.02;
        }
        if (distance > 90000) continue;
        const force = (900 * alpha) / distance;
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      }
    }
    for (const edge of edges) {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      if (!source || !target) continue;
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = ((distance - 46) / distance) * 0.06 * alpha;
      source.vx += dx * force;
      source.vy += dy * force;
      target.vx -= dx * force;
      target.vy -= dy * force;
    }
    for (const node of nodes) {
      node.vx -= node.x * 0.004 * alpha;
      node.vy -= node.y * 0.004 * alpha;
      node.x += Math.max(-30, Math.min(30, node.vx));
      node.y += Math.max(-30, Math.min(30, node.vy));
      node.vx *= 0.55;
      node.vy *= 0.55;
    }
  }
  return { nodes, edges, byId, truncated: candidates.length - kept.length };
}

export default function GraphPage() {
  return (
    <RequiresSnapshot what="The dependency graph">
      <GraphView />
    </RequiresSnapshot>
  );
}

function GraphView() {
  const { repository, published, architecture } = useRepo();
  const sha = published?.snapshot_sha ?? "";
  const graph = useResource(`${repository.id}:graph:${sha}`, () => api.getGraph(repository.id, sha));
  const [showSymbols, setShowSymbols] = useState(false);
  const [showPackages, setShowPackages] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const moduleByPath = useMemo(() => {
    const map = new Map<string, number>();
    architecture.data?.modules.forEach((module, index) => module.file_paths.forEach((path) => map.set(path, index)));
    return map;
  }, [architecture.data]);

  const sim = useMemo(
    () => (graph.data ? layout(graph.data, showSymbols, showPackages, (key) => moduleByPath.get(key) ?? -1) : null),
    [graph.data, showSymbols, showPackages, moduleByPath],
  );

  const neighbors = useMemo(() => {
    if (!sim || !selected) return null;
    const set = new Set([selected]);
    for (const edge of sim.edges) {
      if (edge.source === selected) set.add(edge.target);
      if (edge.target === selected) set.add(edge.source);
    }
    return set;
  }, [sim, selected]);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!sim || !needle) return null;
    return new Set(sim.nodes.filter((node) => node.key.toLowerCase().includes(needle)).map((node) => node.id));
  }, [sim, query]);

  if (graph.error) return <Notice tone="error" title="The graph could not be loaded">{graph.error}</Notice>;
  if (!sim) return <Panel><Loading rows={6} /></Panel>;
  if (sim.nodes.length === 0) {
    return <Panel><Empty title="No graph entities">This snapshot has no analyzable JS or TS files.</Empty></Panel>;
  }

  const selectedNode = selected ? sim.byId.get(selected) ?? null : null;
  const outgoing = selectedNode ? sim.edges.filter((edge) => edge.source === selectedNode.id && edge.type === "IMPORTS") : [];
  const incoming = selectedNode ? sim.edges.filter((edge) => edge.target === selectedNode.id && edge.type === "IMPORTS") : [];
  const radius = (node: SimNode) => (node.kind === "FILE" ? 4 + Math.min(9, Math.sqrt(node.degree) * 1.6) : 3);

  function onWheel(event: React.WheelEvent<SVGSVGElement>) {
    const factor = event.deltaY > 0 ? 0.9 : 1.1;
    setView((current) => ({ ...current, k: Math.max(0.2, Math.min(5, current.k * factor)) }));
  }

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0, 1fr) 340px" }}>
      <div className="graph-canvas">
        <svg
          aria-label={`Dependency graph with ${sim.nodes.length} nodes and ${sim.edges.length} edges`}
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
          onWheel={onWheel}
          role="img"
          viewBox="-500 -350 1000 700"
        >
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            {sim.edges.map((edge) => {
              const source = sim.byId.get(edge.source);
              const target = sim.byId.get(edge.target);
              if (!source || !target) return null;
              const active = selected && (edge.source === selected || edge.target === selected);
              return (
                <line
                  className="graph-edge"
                  data-active={Boolean(active)}
                  data-dim={Boolean(selected && !active)}
                  key={edge.id}
                  x1={source.x}
                  x2={target.x}
                  y1={source.y}
                  y2={target.y}
                />
              );
            })}
            {sim.nodes.map((node) => {
              const dim = (neighbors && !neighbors.has(node.id)) || (matches && !matches.has(node.id));
              const showLabel = node.id === selected || (matches?.has(node.id) ?? false) || node.degree > 8 || view.k > 1.8;
              return (
                <g
                  className="graph-node"
                  data-active={node.id === selected}
                  data-dim={Boolean(dim)}
                  key={node.id}
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelected(node.id === selected ? null : node.id);
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  transform={`translate(${node.x} ${node.y})`}
                >
                  <circle
                    fill={node.kind === "FILE" ? moduleColor(node.module) : node.kind === "MODULE" ? "var(--fixative-2)" : "var(--eosin)"}
                    r={radius(node)}
                  >
                    <title>{node.key}</title>
                  </circle>
                  {showLabel && <text x={radius(node) + 3} y={3}>{nodeTitle(node.key)}</text>}
                </g>
              );
            })}
          </g>
        </svg>
        <div className="graph-toolbar">
          <div style={{ width: 240 }}><SearchField label="Find a file" onChange={setQuery} placeholder="Find a file" value={query} /></div>
          <div className="segmented" role="group" aria-label="Layers">
            <button aria-pressed={showSymbols} onClick={() => setShowSymbols((value) => !value)} type="button">Symbols</button>
            <button aria-pressed={showPackages} onClick={() => setShowPackages((value) => !value)} type="button">Packages</button>
          </div>
        </div>
        <div className="graph-zoom">
          <button aria-label="Zoom in" className="icon-button" onClick={() => setView((v) => ({ ...v, k: Math.min(5, v.k * 1.25) }))} type="button">+</button>
          <button aria-label="Zoom out" className="icon-button" onClick={() => setView((v) => ({ ...v, k: Math.max(0.2, v.k / 1.25) }))} type="button">−</button>
          <button aria-label="Reset view" className="icon-button" onClick={() => setView({ x: 0, y: 0, k: 1 })} type="button">⤾</button>
        </div>
      </div>

      <Panel
        title={selectedNode ? nodeTitle(selectedNode.key) : "Graph"}
        description={selectedNode ? <code style={{ overflowWrap: "anywhere" }}>{selectedNode.key}</code> : `${sim.nodes.length} nodes, ${sim.edges.length} edges${sim.truncated ? `, ${sim.truncated} least-connected hidden` : ""}.`}
      >
        {selectedNode ? (
          <div style={{ display: "grid", gap: 16 }}>
            <div className="chip-row">
              <span className="badge badge-hema">{selectedNode.kind.toLowerCase()}</span>
              <span className="badge">{selectedNode.degree} connections</span>
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>Imports ({outgoing.length})</h3>
              {outgoing.length ? outgoing.slice(0, 12).map((edge) => (
                <button className="list-row" key={edge.id} onClick={() => setSelected(edge.target)} style={{ padding: "6px 0" }} type="button">
                  <code className="truncate">{sim.byId.get(edge.target)?.key}</code>
                </button>
              )) : <p className="muted small">No observed imports.</p>}
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>Imported by ({incoming.length})</h3>
              {incoming.length ? incoming.slice(0, 12).map((edge) => (
                <button className="list-row" key={edge.id} onClick={() => setSelected(edge.source)} style={{ padding: "6px 0" }} type="button">
                  <code className="truncate">{sim.byId.get(edge.source)?.key}</code>
                </button>
              )) : <p className="muted small">Nothing in this snapshot imports it.</p>}
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>Source evidence</h3>
              <EvidenceChips ids={selectedNode.evidence} limit={4} />
            </div>
            {selectedNode.kind === "FILE" && (
              <Link className="button button-secondary" href={`/r/${repository.id}/files?path=${encodeURIComponent(selectedNode.key)}`}>Open in files</Link>
            )}
          </div>
        ) : (
          <div style={{ display: "grid", gap: 12 }} className="muted small">
            <p>Click a node to see what it imports and what depends on it. Drag to pan, scroll to zoom.</p>
            <p>Node colour matches the inferred module on the overview genome. Size grows with connections.</p>
            {graph.data && graph.data.diagnostics.length > 0 && <p>{graph.data.diagnostics.length} parse diagnostics were recorded for this snapshot.</p>}
            {graph.data?.limitations.map((item) => <p key={item}>{item}</p>)}
          </div>
        )}
      </Panel>
    </div>
  );
}
