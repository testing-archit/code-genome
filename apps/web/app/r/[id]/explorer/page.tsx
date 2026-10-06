"use client";

import type { ComponentNode, ComponentNodeExtras, ModuleGraph } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ComponentMap, displayName } from "../../../../components/component-map";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { formatTime, relativeTime } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

type Node = ComponentNode & ComponentNodeExtras;

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
  const resource = !selected && requested && (requested.startsWith("datastore:") || requested.startsWith("integration:")) ? requested : null;

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
        description="Components arranged by role: entry points (API, UI), then services, then data access, with data stores and external integrations on the right. Solid arrows are imports, dashed lines are files that change together, dotted lines are inferred data-store and integration use. The bar under each component is its risk."
        flush
      >
        <div className="explorer-canvas">
          <ComponentMap layered links={graph.data.links} nodes={nodes} onSelect={select} selected={selected?.name ?? resource} />
        </div>
        <div className="panel-body" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="limitations">{graph.data.limitations.map((item) => <span key={item}>{item}</span>)}</div>
        </div>
      </Panel>
      {selected ? (
        <Details graph={graph.data} node={selected} onClose={() => select(null)} />
      ) : resource ? (
        <ResourceDetails id={resource} nodes={nodes} onClose={() => select(null)} onSelect={select} />
      ) : (
        <Ranking nodes={nodes} onSelect={select} />
      )}
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

function Details({ graph, node, onClose }: { graph: ModuleGraph; node: Node; onClose: () => void }) {
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
        {node.role && (
          <section>
            <h3 className="detail-heading">Role</h3>
            <div className="chip-row" style={{ alignItems: "center" }}>
              <span className="badge badge-hema">{node.role}</span>
              <span className="badge badge-warn">inferred</span>
            </div>
            {node.role_signal && <p className="muted small" style={{ marginTop: 6 }}>Why: {node.role_signal}</p>}
          </section>
        )}
        {(node.commits !== undefined || node.contributors?.length) && (
          <section>
            <h3 className="detail-heading">History</h3>
            <dl className="kv">
              {node.commits !== undefined && <><dt>Commits</dt><dd>{node.commits}</dd></>}
              {node.bug_fixes !== undefined && <><dt>Bug-fix commits</dt><dd>{node.bug_fixes} <span className="muted small">(keyword rule, merges excluded)</span></dd></>}
              {node.last_changed && <><dt>Last changed</dt><dd title={formatTime(node.last_changed)}>{relativeTime(node.last_changed)}</dd></>}
              {node.bus_factor != null && (
                <>
                  <dt>Bus factor</dt>
                  <dd>
                    <span className={`badge ${node.bus_factor === 1 ? "badge-warn" : ""}`}>{node.bus_factor}</span>{" "}
                    <span className="muted small">
                      {node.bus_factor === 1 ? "one person made half of its commits: knowledge is concentrated" : `${node.bus_factor} people made half of its commits`}
                    </span>
                  </dd>
                </>
              )}
            </dl>
            {node.contributors && node.contributors.length > 0 && (
              <div style={{ marginTop: 10, display: "grid", gap: 4 }}>
                <span className="muted small">Top contributors (Git author names, not verified identities)</span>
                {node.contributors.map((person) => (
                  <div className="row small" key={person.name} style={{ gap: 8 }}>
                    <span className="grow truncate">{person.name}</span>
                    <span className="muted">{person.commits} commits · {Math.round(person.share * 100)}%</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
        {node.datastores && node.datastores.length > 0 && (
          <section>
            <h3 className="detail-heading">Data stores <span className="badge badge-warn">inferred</span></h3>
            {node.datastores.map((store) => (
              <div className="detail-link" key={store.name}>
                <code>{store.name}</code>
                <small className="muted">{store.access.replace("_", "/")} · {store.reads} reads, {store.writes} writes · {store.via} · via {store.packages.join(", ") || "detected calls"}</small>
                <EvidenceChips ids={store.evidence_ids} limit={2} />
              </div>
            ))}
          </section>
        )}
        {node.integrations && node.integrations.length > 0 && (
          <section>
            <h3 className="detail-heading">External integrations</h3>
            {node.integrations.map((integration) => (
              <div className="detail-link" key={integration.name}>
                <code>{integration.name}</code>
                <small className="muted">{[integration.package, integration.host].filter(Boolean).join(" · ")} · {integration.files.length} files</small>
                <EvidenceChips ids={integration.evidence_ids} limit={2} />
              </div>
            ))}
          </section>
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
        <div className="chip-row">
          <Link className="button button-secondary" href={`${base}/ask?q=${encodeURIComponent(`What does the ${node.name} component do?`)}`}>Ask what this component does</Link>
          <Link className="button button-secondary" href={`${base}/genome?focus=${encodeURIComponent(node.name)}`}>Open in genome graph</Link>
        </div>
      </div>
    </Panel>
  );
}

function ResourceDetails({ id, nodes, onClose, onSelect }: { id: string; nodes: Node[]; onClose: () => void; onSelect: (name: string) => void }) {
  const isStore = id.startsWith("datastore:");
  const name = id.slice(id.indexOf(":") + 1);
  const users = nodes.flatMap((node) => {
    const store = isStore ? node.datastores?.find((item) => item.name === name) : undefined;
    const integration = isStore ? undefined : node.integrations?.find((item) => item.name === name);
    const item = store ?? integration;
    return item ? [{ node, detail: store ? `${store.access.replace("_", "/")}, ${store.reads} reads, ${store.writes} writes` : `${item.files.length} files`, evidence: item.evidence_ids }] : [];
  });
  return (
    <Panel
      title={<code style={{ overflowWrap: "anywhere" }}>{displayName({ name: id, paths: [name] })}</code>}
      description={isStore ? "Data store inferred from client libraries and data-access method names." : "External integration detected from imported client packages and literal URLs."}
      actions={<button className="button button-ghost button-small" onClick={onClose} type="button">Close</button>}
    >
      <div style={{ display: "grid", gap: 12 }}>
        <div className="chip-row"><span className="badge badge-warn">inferred</span><span className="badge">{users.length} components</span></div>
        {users.length === 0 ? (
          <p className="muted small">No supporting evidence was identified in the selected scope.</p>
        ) : users.map((user) => (
          <div className="detail-link" key={user.node.name}>
            <button className="list-row" onClick={() => onSelect(user.node.name)} style={{ padding: 0 }} type="button"><code>{user.node.name}</code></button>
            <small className="muted">{user.detail}</small>
            <EvidenceChips ids={user.evidence} limit={2} />
          </div>
        ))}
      </div>
    </Panel>
  );
}
