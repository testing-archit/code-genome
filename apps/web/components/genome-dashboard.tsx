"use client";

import type { AnalysisRun, AnalysisState, GraphProjection, Repository } from "@code-genome/contracts";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { createAnalysis, createRepository, getAnalysis, getGraph, listAnalyses, listRepositories } from "../lib/api";
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
    void getGraph(selectedId, latestRun.snapshot_sha)
      .then((projection) => {
        if (active) setGraph(projection);
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
    setSelectedId(repository.id);
    setIsAdding(false);
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
          <span className="nav-item disabled"><span>Delivery auditor</span><span className="soon">Phase 3</span></span>
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
                    onClick={() => setSelectedId(repository.id)}
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

                {activeGraph && <GraphSummary graph={activeGraph} />}
              </>
            ) : (
              <div className="panel-placeholder"><span>Select a repository to inspect its evidence scope.</span></div>
            )}
          </div>
        </section>

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

function GraphSummary({ graph }: { graph: GraphProjection }) {
  const files = graph.nodes.filter((node) => node.kind === "FILE").length;
  const symbols = graph.nodes.filter((node) => node.kind === "SYMBOL").length;
  const imports = graph.edges.filter((edge) => edge.type === "IMPORTS").length;
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
          <p className="form-note">Credentials are never accepted in repository URLs. Connecting private access arrives in the ingestion milestone.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions"><button className="secondary-action" onClick={onClose} type="button">Cancel</button><button className="primary-action" disabled={saving} type="submit">{saving ? "Registering…" : "Register repository"}</button></div>
        </form>
      </div>
    </div>
  );
}
