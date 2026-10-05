"use client";

import type { ComponentLink, ComponentNode, ComponentNodeExtras } from "@code-genome/contracts";
import { useMemo } from "react";

type MapNode = ComponentNode & ComponentNodeExtras;
type Placed = MapNode & { x: number; y: number; width: number; layer: number; kind: "component" | "datastore" | "integration" };
type ResourceLink = { source: string; target: string; access: string; evidence_ids: string[] };

/* Architecture tiers, top to bottom of a request: entry points, logic, then data. */
const TIER: Record<string, number> = { api: 0, ui: 0, service: 1, contract: 1, util: 1, config: 1, "infra/scripts": 1, test: 1, unknown: 1, data: 2 };

/** Data stores and external integrations used by components, as their own nodes. */
function resources(nodes: MapNode[]) {
  const extra = new Map<string, MapNode & { kind: "datastore" | "integration" }>();
  const links: ResourceLink[] = [];
  for (const node of nodes) {
    for (const store of node.datastores ?? []) {
      const id = `datastore:${store.name}`;
      const current = extra.get(id);
      extra.set(id, { ...(current ?? blank(id, store.name, "Data store detected from client libraries and data-access calls (inferred).")), kind: "datastore", files: (current?.files ?? 0) + store.files.length });
      links.push({ source: node.name, target: id, access: store.access, evidence_ids: store.evidence_ids });
    }
    for (const integration of node.integrations ?? []) {
      const id = `integration:${integration.name}`;
      const current = extra.get(id);
      extra.set(id, { ...(current ?? blank(id, integration.name, `External integration${integration.host ? ` (${integration.host})` : ""} detected from imports and literal URLs.`)), kind: "integration", files: (current?.files ?? 0) + integration.files.length });
      links.push({ source: node.name, target: id, access: "calls", evidence_ids: integration.evidence_ids });
    }
  }
  return { extra: [...extra.values()], links };
}

function blank(id: string, label: string, description: string): MapNode {
  return { name: id, files: 0, risk: null, fan_in: 0, fan_out: 0, externals: [], inferred: true, description, riskiest: [], paths: [label] };
}

export function displayName(node: { name: string; paths: string[] }): string {
  return node.name.startsWith("datastore:") || node.name.startsWith("integration:") ? node.paths[0] ?? node.name : node.name;
}

const NODE_HEIGHT = 46;
const LAYER_GAP = 72;
const ROW_GAP = 18;

/**
 * Layered layout: a component sits one layer to the right of everything that imports it,
 * so dependencies read left to right. Import cycles are collapsed before layering so the
 * layout always terminates and stays deterministic.
 */
function layout(components: MapNode[], links: ComponentLink[], compact: boolean, layered: boolean) {
  const resourceNodes = layered ? resources(components).extra : [];
  const nodes: MapNode[] = [...components, ...resourceNodes];
  const kindOf = new Map<string, Placed["kind"]>(resourceNodes.map((node) => [node.name, node.kind]));
  const names = nodes.map((node) => node.name);
  const imports = links.filter((link) => link.imports > 0 && link.source !== link.target);
  const outgoing = new Map<string, string[]>(names.map((name) => [name, []]));
  for (const link of imports) outgoing.get(link.source)?.push(link.target);

  // Tarjan's strongly connected components, then longest-path layering on the DAG.
  let index = 0;
  const order = new Map<string, number>();
  const low = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const group = new Map<string, number>();
  let groups = 0;
  const visit = (name: string) => {
    order.set(name, index);
    low.set(name, index);
    index += 1;
    stack.push(name);
    onStack.add(name);
    for (const next of outgoing.get(name) ?? []) {
      if (!order.has(next)) {
        visit(next);
        low.set(name, Math.min(low.get(name)!, low.get(next)!));
      } else if (onStack.has(next)) {
        low.set(name, Math.min(low.get(name)!, order.get(next)!));
      }
    }
    if (low.get(name) === order.get(name)) {
      let member: string | undefined;
      do {
        member = stack.pop()!;
        onStack.delete(member);
        group.set(member, groups);
      } while (member !== name);
      groups += 1;
    }
  };
  names.forEach((name) => order.has(name) || visit(name));

  const layerOf = new Map<number, number>();
  const groupEdges = new Map<number, Set<number>>();
  for (const link of imports) {
    const from = group.get(link.source);
    const to = group.get(link.target);
    if (from === undefined || to === undefined || from === to) continue;
    if (!groupEdges.has(from)) groupEdges.set(from, new Set());
    groupEdges.get(from)!.add(to);
  }
  // Tarjan emits groups in reverse topological order, so walk them from last to first.
  for (let current = groups - 1; current >= 0; current -= 1) {
    if (!layerOf.has(current)) layerOf.set(current, 0);
    for (const next of groupEdges.get(current) ?? []) {
      layerOf.set(next, Math.max(layerOf.get(next) ?? 0, layerOf.get(current)! + 1));
    }
  }

  if (layered) {
    // Role tiers replace import layering when roles separate entry points, logic, and data;
    // otherwise keep import layers. Data stores and integrations always get the last column.
    const tiers = new Set(components.map((node) => TIER[node.role ?? "unknown"] ?? 1));
    const importLayer = new Map(components.map((node) => [node.name, layerOf.get(group.get(node.name) ?? 0) ?? 0]));
    const last = tiers.size > 1 ? 3 : Math.max(0, ...importLayer.values()) + 1;
    layerOf.clear();
    nodes.forEach((node, position) => {
      const layer = kindOf.has(node.name) ? last : tiers.size > 1 ? (TIER[node.role ?? "unknown"] ?? 1) : (importLayer.get(node.name) ?? 0);
      group.set(node.name, -1 - position);
      layerOf.set(-1 - position, layer);
    });
  }

  // Wide enough for the label and the "N files, risk N%" line, whichever is longer.
  const width = (name: string) => Math.min(compact ? 170 : 230, 26 + Math.max(label(name, compact).length * 7.2, 21 * 6.2));
  const layers = new Map<number, MapNode[]>();
  for (const node of nodes) {
    const layer = layerOf.get(group.get(node.name) ?? 0) ?? 0;
    if (!layers.has(layer)) layers.set(layer, []);
    layers.get(layer)!.push(node);
  }
  const columnWidth = Math.max(...nodes.map((node) => width(node.name)), 120);
  const placed: Placed[] = [];
  const tallest = Math.max(...[...layers.values()].map((column) => column.length), 1);
  const height = tallest * (NODE_HEIGHT + ROW_GAP) - ROW_GAP + 24;
  [...layers.entries()]
    .sort(([left], [right]) => left - right)
    .forEach(([layer, column], position) => {
      const sorted = [...column].sort((left, right) => (right.risk ?? 0) - (left.risk ?? 0) || left.name.localeCompare(right.name));
      const columnHeight = sorted.length * (NODE_HEIGHT + ROW_GAP) - ROW_GAP;
      sorted.forEach((node, row) => {
        placed.push({
          ...node,
          kind: kindOf.get(node.name) ?? "component",
          layer,
          width: width(node.name),
          x: 12 + position * (columnWidth + LAYER_GAP),
          y: 12 + (height - 24 - columnHeight) / 2 + row * (NODE_HEIGHT + ROW_GAP),
        });
      });
    });
  const totalWidth = 24 + layers.size * columnWidth + Math.max(0, layers.size - 1) * LAYER_GAP;
  return { placed, width: Math.max(totalWidth, 240), height: Math.max(height, NODE_HEIGHT + 24) };
}

function label(name: string, compact: boolean): string {
  if (name.startsWith("datastore:") || name.startsWith("integration:")) name = name.slice(name.indexOf(":") + 1);
  const parts = name.split("/");
  const short = compact && parts.length > 2 ? parts.slice(-2).join("/") : name;
  const limit = compact ? 20 : 28;
  return short.length > limit ? `…${short.slice(-(limit - 1))}` : short;
}

export function ComponentMap({
  nodes,
  links,
  selected,
  onSelect,
  compact = false,
  layered = false,
}: {
  nodes: MapNode[];
  links: ComponentLink[];
  selected?: string | null;
  onSelect?: (name: string) => void;
  compact?: boolean;
  /** Arrange by architecture role and draw data stores and integrations as nodes. */
  layered?: boolean;
}) {
  const hasRoles = layered && nodes.some((node) => node.role);
  const { placed, width, height } = useMemo(() => layout(nodes, links, compact, hasRoles), [nodes, links, compact, hasRoles]);
  const resourceLinks = useMemo(() => (hasRoles ? resources(nodes).links : []), [nodes, hasRoles]);
  const position = new Map(placed.map((node) => [node.name, node]));
  const heaviest = Math.max(1, ...links.map((link) => link.imports));
  const related = new Set(
    selected
      ? [...links, ...resourceLinks].filter((link) => link.source === selected || link.target === selected).flatMap((link) => [link.source, link.target])
      : [],
  );

  return (
    <svg
      aria-label={`Component map: ${nodes.length} components, ${links.length} links`}
      className="component-map"
      role="img"
      viewBox={`0 0 ${width} ${height}`}
      style={{ width, maxWidth: compact ? undefined : "none" }}
    >
      <defs>
        <marker id="cm-arrow" markerHeight="8" markerUnits="userSpaceOnUse" markerWidth="8" orient="auto" refX="7" refY="4" viewBox="0 0 8 8">
          <path d="M0 0 8 4 0 8z" fill="var(--rule-strong)" />
        </marker>
      </defs>
      {links.map((link) => {
        const from = position.get(link.source);
        const to = position.get(link.target);
        if (!from || !to) return null;
        const forward = to.x > from.x;
        const x1 = forward ? from.x + from.width : from.x + from.width / 2;
        const y1 = forward ? from.y + NODE_HEIGHT / 2 : from.y + NODE_HEIGHT;
        const x2 = forward ? to.x - 4 : to.x + to.width / 2;
        const y2 = forward ? to.y + NODE_HEIGHT / 2 : to.y - 4;
        const bend = forward ? (x2 - x1) / 2 : 40;
        const path = forward
          ? `M${x1} ${y1} C${x1 + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}`
          : `M${x1} ${y1} C${x1 + bend} ${y1 + bend} ${x2 + bend} ${y2 - bend} ${x2} ${y2}`;
        const active = selected ? link.source === selected || link.target === selected : false;
        return (
          <path
            className="component-link"
            d={path}
            data-active={active}
            data-dimmed={Boolean(selected) && !active}
            fill="none"
            key={`${link.source}->${link.target}`}
            markerEnd={link.imports ? "url(#cm-arrow)" : undefined}
            strokeDasharray={link.imports ? undefined : "4 4"}
            strokeWidth={link.imports ? 1 + 3 * Math.sqrt(link.imports / heaviest) : 1.25}
          >
            <title>
              {`${link.source} → ${link.target}: ${link.imports} imports${link.co_changes ? `, co-changed ${link.co_changes}×` : ""}`}
            </title>
          </path>
        );
      })}
      {resourceLinks.map((link) => {
        const from = position.get(link.source);
        const to = position.get(link.target);
        if (!from || !to) return null;
        const x1 = from.x + from.width;
        const y1 = from.y + NODE_HEIGHT / 2;
        const x2 = to.x - 4;
        const y2 = to.y + NODE_HEIGHT / 2;
        const bend = (x2 - x1) / 2;
        const active = selected ? link.source === selected || link.target === selected : false;
        return (
          <path
            className="component-link resource-link"
            d={`M${x1} ${y1} C${x1 + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}`}
            data-active={active}
            data-dimmed={Boolean(selected) && !active}
            fill="none"
            key={`${link.source}->${link.target}`}
            strokeDasharray="2 4"
            strokeWidth={1.5}
          >
            <title>{`${link.source} → ${displayName(to)}: ${link.access} (inferred)`}</title>
          </path>
        );
      })}
      {placed.map((node) => {
        const risk = node.risk ?? 0;
        const isSelected = node.name === selected;
        const dimmed = Boolean(selected) && !isSelected && !related.has(node.name);
        return (
          <g
            aria-label={node.kind === "component" ? `${node.name}, ${node.files} files${node.risk === null ? "" : `, risk ${Math.round(risk * 100)}%`}` : `${node.kind === "datastore" ? "Data store" : "Integration"} ${displayName(node)}`}
            className="component-node"
            data-kind={node.kind}
            data-dimmed={dimmed}
            data-selected={isSelected}
            key={node.name}
            onClick={onSelect ? () => onSelect(node.name) : undefined}
            onKeyDown={onSelect ? (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), onSelect(node.name)) : undefined}
            role={onSelect ? "button" : undefined}
            tabIndex={onSelect ? 0 : undefined}
            transform={`translate(${node.x} ${node.y})`}
          >
            <rect height={NODE_HEIGHT} rx={10} width={node.width} />
            {node.kind === "component" && <rect className="component-risk" height={4} rx={2} width={Math.max(4, (node.width - 20) * risk)} x={10} y={NODE_HEIGHT - 9} />}
            <text x={12} y={19}>{label(node.name, compact)}</text>
            <text className="component-meta" x={12} y={33}>
              {node.kind === "datastore"
                ? "data store (inferred)"
                : node.kind === "integration"
                  ? "external integration"
                  : `${node.role && hasRoles ? `${node.role} · ` : ""}${node.files} ${node.files === 1 ? "file" : "files"}${node.risk === null ? "" : `, risk ${Math.round(risk * 100)}%`}`}
            </text>
            <title>{`${node.name}\n${node.description}`}</title>
          </g>
        );
      })}
    </svg>
  );
}
