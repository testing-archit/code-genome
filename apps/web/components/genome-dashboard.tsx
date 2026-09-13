"use client";

import type {
  AnalysisRun,
  AnalysisState,
  Architecture,
  DeliveryReport,
  Evidence,
  GraphProjection,
  Repository,
  RepositoryInventory,
} from "@code-genome/contracts";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  assessDeliveryReport,
  createAnalysis,
  createDeliveryReport,
  createRepository,
  downloadDeliveryReport,
  getAnalysis,
  getArchitecture,
  getEvidence,
  getGraph,
  getRepositoryInventory,
  listAnalyses,
  listRepositories,
} from "../lib/api";
import { BranchIcon, HelixMark, PlusIcon } from "./icons";

const terminalStates: AnalysisState[] = ["SUCCEEDED", "FAILED"];

function stateLabel(state: AnalysisState) {
  return state.charAt(0) + state.slice(1).toLowerCase();
}

function formatTime(value: string | null) {
  if (!value) return "Not available";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function GenomeDashboard() {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [graph, setGraph] = useState<GraphProjection | null>(null);
  const [inventory, setInventory] = useState<RepositoryInventory | null>(null);
  const [architecture, setArchitecture] = useState<Architecture | null>(null);
  const [isAdding, setIsAdding] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => repositories.find((repository) => repository.id === selectedId) ?? null,
    [repositories, selectedId],
  );
  const latestRun = runs[0] ?? null;
  const activeGraph = graph?.scope.repository_id === selectedId ? graph : null;
  const activeInventory = inventory?.repository_id === selectedId ? inventory : null;
  const activeArchitecture = architecture?.repository_id === selectedId ? architecture : null;

  useEffect(() => {
    let active = true;
    void listRepositories()
      .then((data) => {
        if (!active) return;
        setRepositories(data);
        setSelectedId(data[0]?.id ?? null);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "Repositories could not be loaded.");
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    void listAnalyses(selectedId).then(setRuns).catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Analysis history could not be loaded.");
    });
  }, [selectedId]);

  useEffect(() => {
    if (!latestRun || terminalStates.includes(latestRun.state)) return;
    const timer = window.setInterval(() => {
      void getAnalysis(latestRun.id)
        .then((updated) => {
          setRuns((current) => [updated, ...current.filter((run) => run.id !== updated.id)]);
        })
        .catch((caught: unknown) => {
          setError(
            caught instanceof Error
              ? caught.message
              : "Analysis status could not be refreshed.",
          );
        });
    }, 900);
    return () => window.clearInterval(timer);
  }, [latestRun]);

  useEffect(() => {
    if (!selectedId || latestRun?.state !== "SUCCEEDED" || !latestRun.snapshot_sha) return;
    let active = true;
    void Promise.all([
      getGraph(selectedId, latestRun.snapshot_sha),
      getRepositoryInventory(selectedId),
    ])
      .then(([projection, repositoryInventory]) => {
        if (active) {
          setGraph(projection);
          setInventory(repositoryInventory);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "Published graph could not be loaded.");
        }
      });
    return () => {
      active = false;
    };
  }, [latestRun?.snapshot_sha, latestRun?.state, selectedId]);

  useEffect(() => {
    if (!selectedId || latestRun?.state !== "SUCCEEDED" || !latestRun.snapshot_sha) return;
    let active = true;
    void getArchitecture(selectedId)
      .then((result) => {
        if (active && result.snapshot_sha === latestRun.snapshot_sha) setArchitecture(result);
      })
      .catch(() => {
        if (active) setArchitecture(null);
      });
    return () => {
      active = false;
    };
  }, [latestRun?.snapshot_sha, latestRun?.state, selectedId]);

  async function startAnalysis() {
    if (!selected) return;
    setIsStarting(true);
    setError(null);
    try {
      const run = await createAnalysis(selected.id, selected.default_branch);
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Analysis could not be queued.");
    } finally {
      setIsStarting(false);
    }
  }

  async function addRepository(cloneUrl: string, branch: string) {
    const repository = await createRepository(cloneUrl, branch);
    setRepositories((current) => [repository, ...current]);
    setRuns([]);
    setGraph(null);
    setInventory(null);
    setArchitecture(null);
    setSelectedId(repository.id);
    setIsAdding(false);
  }

  function selectRepository(repositoryId: string) {
    if (repositoryId === selectedId) return;
    setRuns([]);
    setGraph(null);
    setInventory(null);
    setArchitecture(null);
    setSelectedId(repositoryId);
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="CODE GENOME home">
          <span className="brand-mark"><HelixMark /></span>
          <span>CODE<br />GENOME</span>
        </a>

        <nav aria-label="Primary navigation" className="primary-nav">
          <a className="nav-item active" href="#repositories"><span>Repository map</span><span className="nav-index">1</span></a>
          <a className="nav-item" href="#runs"><span>Analysis runs</span><span className="nav-index">2</span></a>
          <a className="nav-item" href="#architecture"><span>Architecture strata</span><span className="nav-index">3</span></a>
          <a className="nav-item" href="#delivery-auditor"><span>Delivery auditor</span><span className="nav-index">4</span></a>
        </nav>

        <div className="scope-note">
          <span className="scope-pulse" />
          <div><strong>Genome Lab</strong><small>Workspace scope active</small></div>
        </div>
      </aside>

      <section className="workspace" id="top">
        <header className="topbar">
          <div>
            <p className="path">Genome Lab / Repository map</p>
            <h1>Read the system from its evidence.</h1>
          </div>
          <button className="primary-action" onClick={() => setIsAdding(true)} type="button">
            <PlusIcon /> Add repository
          </button>
        </header>

        {error && <div className="error-banner" role="alert"><strong>Action needed</strong><span>{error}</span></div>}

        <section className="repository-layout" id="repositories" aria-label="Repository workspace">
          <div className="repository-list">
            <div className="section-heading">
              <h2>Repositories</h2>
              <span>{repositories.length} registered</span>
            </div>

            {isLoading ? (
              <p className="empty-copy">Loading repository scope…</p>
            ) : repositories.length === 0 ? (
              <div className="empty-state">
                <HelixMark size={42} />
                <h3>No repository evidence yet</h3>
                <p>Register a GitHub repository to establish an auditable analysis scope.</p>
                <button className="text-action" onClick={() => setIsAdding(true)} type="button">Add the first repository</button>
              </div>
            ) : (
              <div className="repo-rows">
                {repositories.map((repository) => (
                  <button
                    className={`repo-row ${selectedId === repository.id ? "selected" : ""}`}
                    key={repository.id}
                    onClick={() => selectRepository(repository.id)}
                    type="button"
                  >
                    <span className="repo-symbol"><BranchIcon /></span>
                    <span className="repo-identity"><strong>{repository.external_id.split("/")[1]}</strong><small>{repository.external_id.split("/")[0]}</small></span>
                    <span className="branch-name">{repository.default_branch}</span>
                    <span className="row-caret">›</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="evidence-panel">
            {selected ? (
              <>
                <div className="evidence-title">
                  <div><span>Selected scope</span><h2>{selected.external_id}</h2></div>
                  <span className="registered-state">Registered</span>
                </div>

                <dl className="scope-grid">
                  <div><dt>Provider</dt><dd>GitHub</dd></div>
                  <div><dt>Tracked branch</dt><dd>{selected.default_branch}</dd></div>
                  <div><dt>Added</dt><dd>{formatTime(selected.created_at)}</dd></div>
                  <div><dt>Repository ID</dt><dd title={selected.id}>{selected.id.slice(0, 13)}…</dd></div>
                </dl>

                <div className="genome-rail" aria-label="Analysis pipeline status">
                  <div className="rail-line" />
                  {[
                    ["Registered", true],
                    ["Queued", latestRun !== null],
                    ["Running", latestRun?.state === "RUNNING" || latestRun?.state === "SUCCEEDED"],
                    ["Published", Boolean(latestRun?.snapshot_sha && latestRun.state === "SUCCEEDED")],
                  ].map(([label, active]) => (
                    <div className={`rail-stage ${active ? "reached" : ""}`} key={String(label)}>
                      <span className="rail-node" /><span>{label}</span>
                    </div>
                  ))}
                </div>

                <div className="analysis-action">
                  <div>
                    <h3>{latestRun ? `Latest run: ${stateLabel(latestRun.state)}` : "Structural genome not started"}</h3>
                    <p>{latestRun?.diagnostics[0] ?? "Start a bounded structural analysis to pin the selected branch and extract evidence-backed JS/TS facts."}</p>
                  </div>
                  <button disabled={isStarting || (latestRun ? !terminalStates.includes(latestRun.state) : false)} onClick={startAnalysis} type="button">
                    {isStarting ? "Queuing…" : latestRun ? "Run again" : "Start analysis"}
                  </button>
                </div>

                {activeGraph && (
                  <GraphSummary
                    graph={activeGraph}
                    inventory={activeInventory}
                    key={activeGraph.scope.snapshot_id}
                  />
                )}
              </>
            ) : (
              <div className="panel-placeholder"><span>Select a repository to inspect its evidence scope.</span></div>
            )}
          </div>
        </section>

        {activeArchitecture && <ArchitectureMap architecture={activeArchitecture} />}

        {selected && <DeliveryAuditor repository={selected} />}

        <section className="runs-section" id="runs">
          <div className="section-heading"><h2>Analysis ledger</h2><span>Immutable run history</span></div>
          <div className="ledger-head"><span>State</span><span>Run</span><span>Scope</span><span>Started</span><span>Result</span></div>
          {runs.length === 0 ? (
            <p className="ledger-empty">No analysis runs exist for this repository.</p>
          ) : runs.map((run) => (
            <div className="ledger-row" key={run.id}>
              <span className={`status status-${run.state.toLowerCase()}`}><i />{stateLabel(run.state)}</span>
              <code>{run.id.slice(4, 12)}</code>
              <span>{run.requested_refs.join(", ")}</span>
              <span>{formatTime(run.started_at)}</span>
              <span>{run.state === "FAILED" ? run.error_code : run.snapshot_sha ?? "Lifecycle only"}</span>
            </div>
          ))}
        </section>
      </section>

      {isAdding && <RepositoryDialog onClose={() => setIsAdding(false)} onSave={addRepository} />}
    </main>
  );
}

function dateInput(daysAgo: number) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - daysAgo);
  return date.toISOString().slice(0, 10);
}

function DeliveryAuditor({ repository }: { repository: Repository }) {
  const [text, setText] = useState("");
  const [from, setFrom] = useState(() => dateInput(30));
  const [to, setTo] = useState(() => dateInput(0));
  const [report, setReport] = useState<DeliveryReport | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const created = await createDeliveryReport(
        repository.id,
        text,
        from,
        to,
        repository.default_branch,
      );
      setReport(await assessDeliveryReport(created.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The report could not be assessed.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="auditor-section" id="delivery-auditor">
      <div className="section-heading">
        <h2>Delivery auditor</h2>
        <span>Claims measured against immutable repository evidence</span>
      </div>
      <div className="auditor-layout">
        <form className="report-input" onSubmit={submit}>
          <div className="auditor-kicker">Submitted narrative / {repository.external_id}</div>
          <label htmlFor="delivery-text">What was delivered?</label>
          <textarea
            id="delivery-text"
            maxLength={100000}
            onChange={(event) => setText(event.target.value)}
            placeholder="Added invoice export. Tests are passing. Deployed to production."
            required
            rows={8}
            value={text}
          />
          <div className="scope-inputs">
            <label>From<input max={to} onChange={(event) => setFrom(event.target.value)} required type="date" value={from} /></label>
            <label>To<input min={from} onChange={(event) => setTo(event.target.value)} required type="date" value={to} /></label>
            <label>Branch<input readOnly value={repository.default_branch} /></label>
          </div>
          <p>CI and deployment claims remain external until trusted provider evidence is connected.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-action" disabled={working} type="submit">
            {working ? "Assessing claims…" : "Run delivery audit"}
          </button>
        </form>
        <div className="claim-ledger" aria-live="polite">
          <div className="claim-ledger-head">
            <div><span>Assessment ledger</span><h3>{report ? `${report.claims.length} atomic claims` : "Awaiting a report"}</h3></div>
            {report && <button onClick={() => void downloadDeliveryReport(report.id)} type="button">Download .md</button>}
          </div>
          {!report ? (
            <div className="auditor-empty"><strong>Exact words stay attached.</strong><span>Every claim retains its source span, status, evidence IDs, and limitations.</span></div>
          ) : (
            <>
              {report.claims.map((claim) => (
                <article className="claim-row" key={claim.id}>
                  <div className="claim-source"><span>#{claim.ordinal + 1} · chars {claim.start_offset}–{claim.end_offset}</span><strong>{claim.original_text}</strong></div>
                  <div className={`claim-status claim-${claim.assessment?.status.toLowerCase()}`}>
                    <strong>{claim.assessment?.status.replaceAll("_", " ")}</strong>
                    <span>{Math.round((claim.assessment?.confidence ?? 0) * 100)}% match</span>
                  </div>
                  <p>{claim.assessment?.rationale}</p>
                  <div className="claim-evidence">
                    {(claim.assessment?.evidence_ids.length ? claim.assessment.evidence_ids : claim.assessment?.limitations ?? []).map((item) => <code key={item}>{item}</code>)}
                  </div>
                </article>
              ))}
              <div className="unreported-band">
                <strong>Unreported change surface</strong><span>{report.unreported_changes.length} paths</span>
              </div>
              {report.unreported_changes.slice(0, 8).map((change) => (
                <div className="unreported-row" key={change.path}>
                  <span title={change.path}>{change.path}</span>
                  <i style={{ width: `${Math.max(4, change.materiality * 100)}%` }} />
                  <small>{change.explanation}</small>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function ArchitectureMap({ architecture }: { architecture: Architecture }) {
  return (
    <section className="architecture-section" id="architecture">
      <div className="section-heading">
        <h2>Architecture strata</h2>
        <span>{architecture.analysis_version} · inferred from repository evidence</span>
      </div>
      <div className="architecture-layout">
        <div className="module-strata">
          {architecture.modules.map((module) => (
            <article key={module.id}>
              <div className="module-band" style={{ "--module-confidence": `${module.confidence * 100}%` } as React.CSSProperties}>
                <strong>{module.name}</strong><span>{module.file_paths.length} files</span>
              </div>
              <p>{module.description}</p>
              <div className="citation-row">
                <span>Inferred · {Math.round(module.confidence * 100)}% confidence</span>
                {module.citations.slice(0, 3).map((citation) => <code key={citation}>{citation.slice(0, 19)}</code>)}
              </div>
            </article>
          ))}
        </div>
        <aside className="evolution-seams">
          <div className="strata-subhead"><h3>Evolutionary seams</h3><span>Observed co-change</span></div>
          {architecture.co_changes.slice(0, 8).map((edge) => (
            <div className="seam-row" key={`${edge.left_path}:${edge.right_path}`}>
              <div><span>{nodeTitle(edge.left_path)}</span><span>{nodeTitle(edge.right_path)}</span></div>
              <i style={{ width: `${Math.max(12, edge.confidence * 100)}%` }} />
              <small>{edge.commit_count} shared commits · {Math.round(edge.confidence * 100)}%</small>
            </div>
          ))}
          <div className="strata-subhead hotspot-head"><h3>Relative hotspots</h3><span>Frequency + churn</span></div>
          {architecture.hotspots.slice(0, 6).map((hotspot) => (
            <div className="hotspot-row" key={hotspot.path}>
              <span title={hotspot.path}>{hotspot.path}</span>
              <strong>{Math.round(hotspot.score * 100)}</strong>
            </div>
          ))}
          <p className="architecture-limit">{architecture.limitations[1]}</p>
        </aside>
      </div>
    </section>
  );
}

function GraphSummary({
  graph,
  inventory,
}: {
  graph: GraphProjection;
  inventory: RepositoryInventory | null;
}) {
  const files = graph.nodes.filter((node) => node.kind === "FILE").length;
  const symbols = graph.nodes.filter((node) => node.kind === "SYMBOL").length;
  const imports = graph.edges.filter((edge) => edge.type === "IMPORTS").length;
  const inspectableNodes = graph.nodes.filter((node) => node.evidence_ids.length > 0);
  const [selectedNodeId, setSelectedNodeId] = useState(inspectableNodes[0]?.id ?? null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const selectedNode = inspectableNodes.find((node) => node.id === selectedNodeId) ?? null;
  const evidenceId = selectedNode?.evidence_ids[0] ?? null;

  useEffect(() => {
    if (!evidenceId) return;
    let active = true;
    void getEvidence(evidenceId)
      .then((item) => {
        if (active) setEvidence(item);
      })
      .catch((caught: unknown) => {
        if (active) {
          setEvidenceError(
            caught instanceof Error ? caught.message : "Evidence could not be resolved.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [evidenceId]);

  function selectNode(nodeId: string) {
    setSelectedNodeId(nodeId);
    setEvidence(null);
    setEvidenceError(null);
  }

  return (
    <div className="graph-summary" aria-label="Published structural graph summary">
      <div className="graph-metrics">
        <div><strong>{files}</strong><span>Source files</span></div>
        <div><strong>{symbols}</strong><span>Symbols</span></div>
        <div><strong>{imports}</strong><span>Imports</span></div>
        <div><strong>{graph.diagnostics.length}</strong><span>Diagnostics</span></div>
      </div>
      <div className="snapshot-line">
        <span>Pinned snapshot</span>
        <code title={graph.scope.snapshot_sha}>{graph.scope.snapshot_sha.slice(0, 12)}</code>
        <span>{graph.scope.analysis_version}</span>
      </div>
      <div className="inventory-line" aria-label="Repository ingestion inventory">
        <span><strong>{inventory?.refs.length ?? "—"}</strong> tracked refs</span>
        <span><strong>{inventory?.commits.length ?? "—"}</strong> commits observed</span>
        <span><strong>{inventory?.files.length ?? "—"}</strong> manifest files</span>
        <span>Read-only bare mirror</span>
      </div>
      <section className="evidence-workbench" aria-labelledby="evidence-lens-title">
        <div className="evidence-index">
          <div className="lens-heading">
            <div><span>Graph index</span><h3>Evidence-backed entities</h3></div>
            <span>{inspectableNodes.length}</span>
          </div>
          <div className="evidence-node-list">
            {inspectableNodes.map((node) => (
              <button
                aria-pressed={node.id === selectedNodeId}
                className={node.id === selectedNodeId ? "active" : ""}
                key={node.id}
                onClick={() => selectNode(node.id)}
                type="button"
              >
                <span className={`node-kind node-kind-${node.kind.toLowerCase()}`}>
                  {node.kind.slice(0, 3)}
                </span>
                <span><strong>{nodeTitle(node.natural_key)}</strong><small>{node.natural_key}</small></span>
                <i>›</i>
              </button>
            ))}
          </div>
        </div>
        <aside className="evidence-lens">
          <div className="lens-heading">
            <div><span>Evidence lens</span><h3 id="evidence-lens-title">Immutable source locator</h3></div>
            <span className="verified-mark">Verified</span>
          </div>
          {evidenceError ? (
            <p className="lens-error" role="alert">{evidenceError}</p>
          ) : evidence?.id === evidenceId ? (
            <EvidenceDetails evidence={evidence} nodeKind={selectedNode?.kind ?? "ENTITY"} />
          ) : (
            <div className="lens-loading"><span />Resolving pinned evidence…</div>
          )}
        </aside>
      </section>
    </div>
  );
}

function nodeTitle(naturalKey: string) {
  const symbol = naturalKey.split("#")[1];
  if (symbol) {
    const [, name] = symbol.split(":");
    if (name) return name;
  }
  const pieces = naturalKey.split(/[/:#]/).filter(Boolean);
  return pieces.at(-1) ?? naturalKey;
}

function EvidenceDetails({ evidence, nodeKind }: { evidence: Evidence; nodeKind: string }) {
  const range = evidence.start_line
    ? `L${evidence.start_line}${evidence.start_column ? `:${evidence.start_column}` : ""}${
        evidence.end_line ? `–L${evidence.end_line}${evidence.end_column ? `:${evidence.end_column}` : ""}` : ""
      }`
    : "Whole file";
  return (
    <div className="evidence-details">
      <div className="locator-path">
        <span>{nodeKind} / {evidence.kind.replaceAll("_", " ")}</span>
        <code title={evidence.path}>{evidence.path}</code>
      </div>
      <dl>
        <div><dt>Source range</dt><dd>{range}</dd></div>
        <div><dt>Pinned commit</dt><dd><code title={evidence.repository_sha}>{evidence.repository_sha.slice(0, 16)}</code></dd></div>
        <div><dt>Extractor</dt><dd>{evidence.extractor_version}</dd></div>
        <div><dt>Observed</dt><dd>{formatTime(evidence.observed_at)}</dd></div>
      </dl>
      <div className="evidence-id-line"><span>Evidence ID</span><code>{evidence.id}</code></div>
      <p>Locator metadata only. Source content stays inside the authorized repository boundary.</p>
    </div>
  );
}

function RepositoryDialog({ onClose, onSave }: { onClose: () => void; onSave: (url: string, branch: string) => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(url, branch);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Repository could not be registered.");
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div aria-labelledby="dialog-title" aria-modal="true" className="dialog" role="dialog">
        <div className="dialog-heading"><div><span>New evidence scope</span><h2 id="dialog-title">Register a repository</h2></div><button aria-label="Close dialog" onClick={onClose} type="button">×</button></div>
        <form onSubmit={submit}>
          <label>GitHub HTTPS URL<input autoFocus onChange={(event) => setUrl(event.target.value)} placeholder="https://github.com/owner/repository" required type="url" value={url} /></label>
          <label>Default branch<input onChange={(event) => setBranch(event.target.value)} required value={branch} /></label>
          <p className="form-note">Credentials are never accepted in repository URLs. Owners can attach encrypted private access through the repository connection API.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions"><button className="secondary-action" onClick={onClose} type="button">Cancel</button><button className="primary-action" disabled={saving} type="submit">{saving ? "Registering…" : "Register repository"}</button></div>
        </form>
      </div>
    </div>
  );
}
