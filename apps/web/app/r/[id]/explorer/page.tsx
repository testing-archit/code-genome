"use client";

import type { ComponentNode, ModuleGraph } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ComponentMap } from "../../../../components/component-map";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { useResource } from "../../../../lib/use-resource";

export default function ExplorerPage() {
  return (
    <RequiresSnapshot what="Architecture explorer">
      <Suspense fallback={<Panel><Loading rows={6} /></Panel>}>
        <Explorer />
      </Suspense>
    </RequiresSnapshot>
  );
}

function Explorer() {
  const { repository, published } = useRepo();
  const router = useRouter();
  const params = useSearchParams();
  const graph = useResource(`${repository.id}:module-graph:${published?.snapshot_sha ?? ""}`, () => api.getModuleGraph(repository.id));
  const requested = params.get("component");
  const nodes = graph.data?.nodes ?? [];
  const selected = nodes.find((node) => node.name === requested) ?? null;

  function select(name: string | null) {
    const next = new URLSearchParams(params.toString());
    if (name && name !== requested) next.set("component", name);
    else next.delete("component");
    const query = next.toString();
    router.replace(`/r/${repository.id}/explorer${query ? `?${query}` : ""}`, { scroll: false });
  }

  if (graph.error) return <Notice tone="error" title="The architecture could not be loaded">{graph.error}</Notice>;
  if (!graph.data) return <Panel><Loading rows={8} /></Panel>;
  if (nodes.length === 0) {
    return <Panel><Empty centered title="No JavaScript or TypeScript components">The explorer maps JS and TS source. Other languages are covered by search, Ask, and the generated docs.</Empty></Panel>;
  }

  return (
    <div className="explorer">
      <Panel
        title="Architecture explorer"
        description="Source files grouped by folder. Solid arrows are imports, dashed lines are files that change together. The bar under each component is its risk."
        flush
      >
        <div className="explorer-canvas">
          <ComponentMap links={graph.data.links} nodes={nodes} onSelect={select} selected={selected?.name ?? null} />
        </div>
        <div className="panel-body" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="limitations">{graph.data.limitations.map((item) => <span key={item}>{item}</span>)}</div>
        </div>
      </Panel>
      {selected ? <Details graph={graph.data} node={selected} onClose={() => select(null)} /> : <Ranking nodes={nodes} onSelect={select} />}
    </div>
  );
}

function Ranking({ nodes, onSelect }: { nodes: ComponentNode[]; onSelect: (name: string) => void }) {
  const ranked = [...nodes].sort((left, right) => (right.risk ?? -1) - (left.risk ?? -1) || right.files - left.files);
  return (
    <Panel title="Components" description="Select one on the map or below to see its files, links, and risk." flush>
      <div className="list">
        {ranked.map((node) => (
          <button className="list-row" key={node.name} onClick={() => onSelect(node.name)} type="button">
            <div className="grow" style={{ minWidth: 0, textAlign: "left" }}>
              <code className="truncate" style={{ display: "block" }}>{node.name}</code>
              <small>{node.files} {node.files === 1 ? "file" : "files"}, used by {node.fan_in}, uses {node.fan_out}</small>
            </div>
            {node.risk !== null && <><Meter tone="eosin" value={node.risk} /><span className="score">{Math.round(node.risk * 100)}%</span></>}
          </button>
        ))}
      </div>
    </Panel>
  );
}

function Details({ graph, node, onClose }: { graph: ModuleGraph; node: ComponentNode; onClose: () => void }) {
  const { repository } = useRepo();
  const base = `/r/${repository.id}`;
  const uses = graph.links.filter((link) => link.source === node.name);
  const usedBy = graph.links.filter((link) => link.target === node.name);
  return (
    <Panel
      title={<code style={{ overflowWrap: "anywhere" }}>{node.name}</code>}
      description={node.description}
      actions={<button className="button button-ghost button-small" onClick={onClose} type="button">Close</button>}
    >
      <div style={{ display: "grid", gap: 18 }}>
        {node.risk !== null && (
          <div className="detail-risk">
            <span>Risk</span><Meter tone="eosin" value={node.risk} /><strong>{Math.round(node.risk * 100)}%</strong>
          </div>
        )}
        <section>
          <h3 className="detail-heading">Depends on</h3>
          {uses.length ? uses.map((link) => (
            <div className="detail-link" key={link.target}>
              <code>{link.target}</code>
              <small className="muted">{link.imports ? `${link.imports} imports` : ""}{link.imports && link.co_changes ? ", " : ""}{link.co_changes ? `changed together ${link.co_changes}×` : ""}</small>
              <EvidenceChips ids={link.evidence_ids} limit={2} />
            </div>
          )) : <p className="muted small">No other component.</p>}
        </section>
        <section>
          <h3 className="detail-heading">Used by</h3>
          {usedBy.length ? usedBy.map((link) => (
            <div className="detail-link" key={link.source}>
              <code>{link.source}</code>
              <small className="muted">{link.imports ? `${link.imports} imports` : ""}{link.imports && link.co_changes ? ", " : ""}{link.co_changes ? `changed together ${link.co_changes}×` : ""}</small>
              <EvidenceChips ids={link.evidence_ids} limit={2} />
            </div>
          )) : <p className="muted small">No other component.</p>}
        </section>
        {node.externals.length > 0 && (
          <section>
            <h3 className="detail-heading">External packages</h3>
            <div className="chip-row">{node.externals.map((name) => <span className="chip" key={name}>{name}</span>)}</div>
          </section>
        )}
        <section>
          <h3 className="detail-heading">Files ({node.files})</h3>
          <div className="detail-files">
            {node.paths.map((path) => (
              <div className="detail-file" key={path}>
                <Link href={`${base}/files?path=${encodeURIComponent(path)}`}><code>{path.startsWith(`${node.name}/`) ? path.slice(node.name.length + 1) : path}</code></Link>
                <Link className="muted small" href={`${base}/change?path=${encodeURIComponent(path)}`}>What breaks?</Link>
              </div>
            ))}
          </div>
        </section>
        <Link className="button button-secondary" href={`${base}/ask?q=${encodeURIComponent(`What does the ${node.name} component do?`)}`}>Ask what this component does</Link>
      </div>
    </Panel>
  );
}
