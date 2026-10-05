"use client";

import type { AnalysisRun, Architecture, Evidence, MlOverview, ProviderSignal, Repository, RepositoryInventory } from "@code-genome/contracts";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../lib/api";
import { formatTime, repoName, shortSha } from "../lib/format";
import { invalidate, Resource, useResource } from "../lib/use-resource";
import { AnalysisProgress } from "./analysis-progress";
import { CloseIcon, HelixMark } from "./icons";
import { sectionFor, TopBar } from "./shell";
import { Empty, Notice } from "./ui";
import { useWorkspace } from "./workspace";

const terminal = new Set(["SUCCEEDED", "FAILED"]);

type RepoValue = {
  repository: Repository;
  runs: AnalysisRun[];
  latestRun: AnalysisRun | null;
  /** The newest successful run with a pinned snapshot. Data views read from this. */
  published: AnalysisRun | null;
  startAnalysis: () => Promise<void>;
  starting: boolean;
  inventory: Resource<RepositoryInventory>;
  architecture: Resource<Architecture>;
  models: Resource<MlOverview>;
  trainModels: () => Promise<void>;
  training: boolean;
  openEvidence: (evidenceId: string) => void;
  /** Set when the run list failed to load; views must not claim "not analyzed" then. */
  runsError: string | null;
  runsLoaded: boolean;
  retryRuns: () => void;
};

const RepoContext = createContext<RepoValue | null>(null);

export function useRepo(): RepoValue {
  const value = useContext(RepoContext);
  if (!value) throw new Error("useRepo must be used inside RepoProvider");
  return value;
}

export function RepoProvider({ repositoryId, children }: { repositoryId: string; children: React.ReactNode }) {
  const { repositories, loading, setRepositoryState, toast } = useWorkspace();
  const repository = repositories.find((item) => item.id === repositoryId) ?? null;
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [training, setTraining] = useState(false);

  const [runsAttempt, setRunsAttempt] = useState(0);
  const [runsLoaded, setRunsLoaded] = useState(false);
  const retryRuns = useCallback(() => setRunsAttempt((value) => value + 1), []);
  useEffect(() => {
    let active = true;
    api
      .listAnalyses(repositoryId)
      .then((items) => {
        if (!active) return;
        setRuns(items);
        setRunsError(null);
        setRunsLoaded(true);
      })
      .catch((caught: unknown) => {
        if (active) setRunsError(errorMessage(caught, "Analysis history could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, [repositoryId, runsAttempt]);

  const latestRun = runs[0] ?? null;
  // Views read the branch head; a past point analysed later is for comparison only.
  const published = runs.find((run) => run.state === "SUCCEEDED" && run.snapshot_sha && !run.as_of) ?? null;

  useEffect(() => {
    setRepositoryState(repositoryId, latestRun?.state);
  }, [latestRun?.state, repositoryId, setRepositoryState]);

  const pollingRunId = latestRun && !terminal.has(latestRun.state) ? latestRun.id : null;
  useEffect(() => {
    if (!pollingRunId) return;
    // One request in flight at a time, backing off from 2s to 5s, so a long analysis
    // never pushes the browser past the API rate limit.
    let active = true;
    let delay = 2000;
    let timer = 0;
    const poll = () => {
      api
        .getAnalysis(pollingRunId)
        .then((updated) => {
          if (!active) return;
          setRuns((current) => [updated, ...current.filter((run) => run.id !== updated.id)]);
          if (updated.state === "SUCCEEDED") {
            invalidate(`${repositoryId}:`);
            toast(updated.as_of ? `Snapshot as of ${updated.as_of.length === 40 ? updated.as_of.slice(0, 8) : updated.as_of} published. Compare it on Compare snapshots.` : "Analysis finished. Views now show the new snapshot.");
          } else if (updated.state === "FAILED") {
            toast("Analysis failed. See the run details on the overview.");
          }
        })
        .catch(() => undefined)
        .finally(() => {
          if (!active) return;
          delay = Math.min(5000, delay + 500);
          timer = window.setTimeout(poll, delay);
        });
    };
    timer = window.setTimeout(poll, delay);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [pollingRunId, repositoryId, toast]);

  const startAnalysis = useCallback(async () => {
    if (!repository) return;
    setStarting(true);
    try {
      const run = await api.createAnalysis(repository.id, repository.default_branch);
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      toast("Analysis queued");
    } catch (caught) {
      toast(errorMessage(caught, "The analysis could not be queued."));
    } finally {
      setStarting(false);
    }
  }, [repository, toast]);

  const sha = published?.snapshot_sha ?? null;
  const inventory = useResource(sha ? `${repositoryId}:inventory:${sha}` : null, () => api.getInventory(repositoryId));
  const architecture = useResource(sha ? `${repositoryId}:architecture:${sha}` : null, () => api.getArchitecture(repositoryId));
  const models = useResource(sha ? `${repositoryId}:ml:${sha}` : null, () => api.getModels(repositoryId));
  const reloadModels = models.reload;

  const trainModels = useCallback(async () => {
    setTraining(true);
    try {
      const result = await api.trainModels(repositoryId);
      const trained = Object.values(result.tasks).filter((task) => task?.status === "trained").length;
      invalidate(`${repositoryId}:risk:`);
      invalidate(`${repositoryId}:impact:`);
      reloadModels();
      toast(`Trained ${trained} of ${Object.keys(result.tasks).length} models on this snapshot`);
    } catch (caught) {
      toast(errorMessage(caught, "Models could not be trained."));
    } finally {
      setTraining(false);
    }
  }, [repositoryId, reloadModels, toast]);

  const value = useMemo<RepoValue | null>(
    () =>
      repository
        ? { repository, runs, latestRun, published, startAnalysis, starting, inventory, architecture, models, trainModels, training, openEvidence: setEvidenceId, runsError, runsLoaded, retryRuns }
        : null,
    [repository, runs, latestRun, published, startAnalysis, starting, inventory, architecture, models, trainModels, training, runsError, runsLoaded, retryRuns],
  );

  if (!value) {
    return (
      <>
        <TopBar title="Repository" />
        <div className="content">
          {loading ? (
            <div className="skeleton" style={{ height: 180 }} />
          ) : (
            <div className="panel">
              <Empty title="This repository is not in your workspace" action={<Link className="button button-primary" href="/">See all repositories</Link>}>
                It may have been removed, or the link points to another workspace.
              </Empty>
            </div>
          )}
        </div>
      </>
    );
  }

  return (
    <RepoContext.Provider value={value}>
      <RepoFrame runsError={runsError}>{children}</RepoFrame>
      {evidenceId && <EvidenceDrawer evidenceId={evidenceId} onClose={() => setEvidenceId(null)} />}
    </RepoContext.Provider>
  );
}

function RepoFrame({ children, runsError }: { children: React.ReactNode; runsError: string | null }) {
  const { repository, latestRun, published, retryRuns, inventory, architecture, models } = useRepo();
  const pathname = usePathname();
  const base = `/r/${repository.id}`;
  const { owner, name } = repoName(repository.external_id);
  const running = latestRun && !terminal.has(latestRun.state);
  const section = sectionFor(pathname, repository.id);

  return (
    <>
      <TopBar
        title={<Link className="topbar-repo" href={base}>{name}</Link>}
        detail={
          <>
            {section.group ? `${section.group} / ${section.label}` : `${owner}/${name}`}
            {" "}on <code>{repository.default_branch}</code>
            {published && <> at <code>{shortSha(published.snapshot_sha, 8)}</code></>}
          </>
        }
        actions={running ? <span className="badge badge-warn">Analyzing {Math.round((latestRun?.progress ?? 0) * 100)}%</span> : null}
      />
      <div className="content">
        {runsError && (
          <Notice tone="error" title="Analysis history could not be loaded">
            {runsError}{" "}
            <button className="link-button" onClick={retryRuns} type="button">Try again</button>
          </Notice>
        )}
        {[inventory, architecture, models].some((resource) => resource.error) && (
          <Notice tone="error" title="Some repository data could not be loaded">
            {[inventory, architecture, models].find((resource) => resource.error)?.error}{" "}
            <button className="link-button" onClick={() => [inventory, architecture, models].forEach((resource) => resource.error && resource.reload())} type="button">Try again</button>
          </Notice>
        )}
        {children}
      </div>
    </>
  );
}

/** Wraps a view that needs a published snapshot and explains how to get one when missing. */
export function RequiresSnapshot({ children, what }: { children: React.ReactNode; what: string }) {
  const { published, latestRun, startAnalysis, starting, runsError, runsLoaded } = useRepo();
  if (published) return <>{children}</>;
  if (runsError) return null; // The frame already shows the error with a retry.
  if (!runsLoaded) return <div className="panel"><div className="skeleton" style={{ height: 160 }} /></div>;
  const running = latestRun && !terminal.has(latestRun.state);
  return (
    <div className="panel">
      <Empty
        centered
        icon={<HelixMark size={36} />}
        title={running ? "Analysis in progress" : "No published snapshot yet"}
        action={
          running ? (
            latestRun ? <div style={{ width: "min(560px, 100%)", textAlign: "left" }}><AnalysisProgress compact run={latestRun} /></div> : null
          ) : (
            <button className="button button-primary" disabled={starting} onClick={() => void startAnalysis()} type="button">
              {starting ? "Queuing…" : "Analyze repository"}
            </button>
          )
        }
      >
        {running
          ? `${what} appears here once the current analysis publishes a snapshot.`
          : `Analyze the tracked branch to see ${what.toLowerCase()}. Analysis pins a commit so every result links back to it.`}
      </Empty>
    </div>
  );
}

function EvidenceDrawer({ evidenceId, onClose }: { evidenceId: string; onClose: () => void }) {
  const { inventory, architecture, repository } = useRepo();
  const [kind, ...rest] = evidenceId.split(":");
  const value = rest.join(":");
  const isProvenance = kind === "evidence" || (!evidenceId.includes(":") && kind !== "file");
  const provenanceId = kind === "evidence" ? value : evidenceId;
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isProvenance) return;
    let active = true;
    api
      .getEvidence(provenanceId)
      .then((item) => {
        if (active) setEvidence(item);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught, "This evidence could not be resolved."));
      });
    return () => {
      active = false;
    };
  }, [isProvenance, provenanceId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  let body: React.ReactNode;
  if (kind === "commit") {
    const commit = inventory.data?.commits.find((item) => item.sha === value);
    body = commit ? (
      <>
        <p style={{ fontWeight: 600, whiteSpace: "pre-wrap" }}>{commit.message}</p>
        <dl className="kv">
          <dt>Commit</dt><dd><code>{commit.sha}</code></dd>
          <dt>Author</dt><dd>{commit.author_name}</dd>
          <dt>Authored</dt><dd>{formatTime(commit.authored_at)}</dd>
          <dt>Parents</dt><dd>{commit.parent_shas.map((sha) => <code key={sha}>{shortSha(sha)} </code>)}</dd>
        </dl>
        <a className="button button-secondary" href={`${repository.clone_url.replace(/\.git$/, "")}/commit/${commit.sha}`} rel="noreferrer" target="_blank">Open on GitHub</a>
      </>
    ) : (
      <dl className="kv"><dt>Commit</dt><dd><code>{value}</code></dd><dt>Note</dt><dd>This commit is outside the loaded history window.</dd></dl>
    );
  } else if (kind === "module") {
    const found = architecture.data?.modules.find((item) => item.id === value);
    body = found ? (
      <>
        <p>{found.description}</p>
        <dl className="kv">
          <dt>Module</dt><dd>{found.name}</dd>
          <dt>Confidence</dt><dd>{Math.round(found.confidence * 100)}% · inferred from structure and co-change</dd>
          <dt>Files</dt><dd>{found.file_paths.slice(0, 20).map((path) => <div key={path}><code>{path}</code></div>)}{found.file_paths.length > 20 && <span className="muted">and {found.file_paths.length - 20} more</span>}</dd>
        </dl>
      </>
    ) : <p className="muted">Module <code>{value}</code> is not part of the current snapshot.</p>;
  } else if (kind === "file") {
    body = (
      <>
        <dl className="kv"><dt>File</dt><dd><code>{value}</code></dd><dt>Source</dt><dd>Analyzed file in the published snapshot</dd></dl>
        <Link className="button button-secondary" href={`/r/${repository.id}/files?path=${encodeURIComponent(value)}`} onClick={onClose}>Open in files</Link>
      </>
    );
  } else if (kind === "hotspot") {
    const hotspot = architecture.data?.hotspots.find((item) => item.path === value);
    body = (
      <dl className="kv">
        <dt>File</dt><dd><code>{value}</code></dd>
        {hotspot && <><dt>Commits</dt><dd>{hotspot.commit_count}</dd><dt>Churn</dt><dd>{hotspot.churn} lines</dd><dt>Score</dt><dd>{Math.round(hotspot.score * 100)} relative</dd></>}
      </dl>
    );
  } else if (kind === "change") {
    const separator = value.indexOf(":");
    const sha = separator === -1 ? value : value.slice(0, separator);
    const path = separator === -1 ? "" : value.slice(separator + 1);
    const commit = inventory.data?.commits.find((item) => item.sha === sha);
    body = (
      <>
        <dl className="kv">
          <dt>Changed file</dt><dd><code>{path || "unknown"}</code></dd>
          <dt>Commit</dt><dd><code>{sha}</code></dd>
          {commit && <><dt>Message</dt><dd style={{ whiteSpace: "pre-wrap" }}>{commit.message.split("\n")[0]}</dd><dt>Author</dt><dd>{commit.author_name}, {formatTime(commit.authored_at)}</dd></>}
        </dl>
        <a className="button button-secondary" href={`${repository.clone_url.replace(/\.git$/, "")}/commit/${encodeURIComponent(sha)}`} rel="noreferrer" target="_blank">Open the commit on GitHub</a>
      </>
    );
  } else if (kind === "provider") {
    body = <ProviderEvidence signalId={value} />;
  } else if (!isProvenance) {
    body = <dl className="kv"><dt>Reference</dt><dd><code>{evidenceId}</code></dd><dt>Note</dt><dd>This kind of evidence has no detail view yet.</dd></dl>;
  } else if (error) {
    body = <Notice tone="error" title="Evidence unavailable">{error}</Notice>;
  } else if (!evidence) {
    body = <div className="skeleton" style={{ height: 140 }} />;
  } else {
    const range = evidence.start_line
      ? `Lines ${evidence.start_line}${evidence.end_line && evidence.end_line !== evidence.start_line ? `–${evidence.end_line}` : ""}`
      : "Whole file";
    const lineAnchor = evidence.start_line ? `#L${evidence.start_line}${evidence.end_line ? `-L${evidence.end_line}` : ""}` : "";
    // GitHub renders Markdown, which ignores line anchors unless the plain view is requested.
    const plain = /\.(md|mdx|markdown)$/i.test(evidence.path) && lineAnchor ? "?plain=1" : "";
    const sourcePath = evidence.path.split("/").map(encodeURIComponent).join("/");
    body = (
      <>
        <dl className="kv">
          <dt>File</dt><dd><code>{evidence.path}</code></dd>
          <dt>Location</dt><dd>{range}</dd>
          <dt>Kind</dt><dd>{evidence.kind === "knowledge" ? "cited excerpt" : evidence.kind.replaceAll("_", " ")}</dd>
          {evidence.heading && <><dt>Section</dt><dd>{evidence.heading}</dd></>}
          <dt>Pinned commit</dt><dd><code>{evidence.repository_sha}</code></dd>
          <dt>Extractor</dt><dd>{evidence.extractor_version}</dd>
          <dt>Observed</dt><dd>{formatTime(evidence.observed_at)}</dd>
        </dl>
        {evidence.excerpt && (
          <pre className="evidence-excerpt" tabIndex={0} aria-label="Cited excerpt">{evidence.excerpt}</pre>
        )}
        <a className="button button-secondary" href={`${repository.clone_url.replace(/\.git$/, "")}/blob/${evidence.repository_sha}/${sourcePath}${plain}${lineAnchor}`} rel="noreferrer" target="_blank">
          View source at this commit
        </a>
      </>
    );
  }

  return (
    <>
      <div className="overlay" onClick={onClose} role="presentation" />
      <aside aria-labelledby="evidence-title" aria-modal="true" className="drawer" role="dialog">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <h2 id="evidence-title">Evidence</h2>
            <code className="muted small" style={{ overflowWrap: "anywhere" }}>{evidenceId}</code>
          </div>
          <button aria-label="Close evidence" autoFocus className="icon-button" onClick={onClose} type="button"><CloseIcon /></button>
        </div>
        <div className="drawer-body">{body}</div>
      </aside>
    </>
  );
}

function ProviderEvidence({ signalId }: { signalId: string }) {
  const [signal, setSignal] = useState<ProviderSignal | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api
      .getProviderSignal(signalId)
      .then((item) => active && setSignal(item))
      .catch((caught: unknown) => active && setError(errorMessage(caught, "This evidence could not be loaded.")));
    return () => {
      active = false;
    };
  }, [signalId]);
  if (error) return <Notice tone="error" title="Evidence unavailable">{error}</Notice>;
  if (!signal) return <div className="skeleton" style={{ height: 120 }} />;
  const passed = signal.outcome === "success";
  return (
    <>
      <dl className="kv">
        <dt>{signal.kind === "ci_run" ? "CI check" : "Deployment"}</dt><dd>{signal.name}</dd>
        <dt>Outcome</dt><dd><span className={`badge ${passed ? "badge-ok" : "badge-warn"}`}>{signal.outcome}</span></dd>
        {signal.environment && <><dt>Environment</dt><dd>{signal.environment}</dd></>}
        <dt>Commit</dt><dd><code>{signal.commit_sha}</code></dd>
        {signal.observed_at && <><dt>Recorded</dt><dd>{formatTime(signal.observed_at)}</dd></>}
        <dt>Source</dt><dd>GitHub ({signal.analysis_version}), read {formatTime(signal.fetched_at)}</dd>
      </dl>
      {signal.url && <a className="button button-secondary" href={signal.url} rel="noreferrer" target="_blank">Open on GitHub</a>}
    </>
  );
}

export function EvidenceChips({ ids, limit = 8 }: { ids: string[]; limit?: number }) {
  const { openEvidence } = useRepo();
  if (ids.length === 0) return null;
  return (
    <div className="chip-row">
      {ids.slice(0, limit).map((id) => (
        <button className="chip" key={id} onClick={() => openEvidence(id)} title={`Inspect ${id}`} type="button">
          <span className="truncate" style={{ maxWidth: 220 }}>{chipLabel(id)}</span>
        </button>
      ))}
      {ids.length > limit && <span className="muted small">+{ids.length - limit} more</span>}
    </div>
  );
}

function chipLabel(id: string): string {
  const [kind, ...rest] = id.split(":");
  const value = rest.join(":");
  if (kind === "commit") return `commit ${value.slice(0, 8)}`;
  if (kind === "hotspot") return value.split("/").slice(-2).join("/");
  if (kind === "change") return `change ${value.slice(0, 8)} ${value.split("/").pop() ?? ""}`.trim();
  if (kind === "module") return `module ${value.slice(0, 14)}`;
  if (kind === "evidence") return `source ${value.slice(0, 12)}`;
  if (kind === "provider") return "CI / deploy";
  if (kind === "file") return value.split("/").slice(-2).join("/");
  return id.slice(0, 20);
}
