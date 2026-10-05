"use client";

import type { AnalysisRun, Architecture, Evidence, MlOverview, Repository, RepositoryInventory } from "@code-genome/contracts";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../lib/api";
import { formatTime, repoName, shortSha } from "../lib/format";
import { invalidate, Resource, useResource } from "../lib/use-resource";
import { CloseIcon, HelixMark } from "./icons";
import { repoSections, TopBar } from "./shell";
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

  useEffect(() => {
    let active = true;
    api
      .listAnalyses(repositoryId)
      .then((items) => {
        if (active) setRuns(items);
      })
      .catch((caught: unknown) => {
        if (active) setRunsError(errorMessage(caught, "Analysis history could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, [repositoryId]);

  const latestRun = runs[0] ?? null;
  const published = runs.find((run) => run.state === "SUCCEEDED" && run.snapshot_sha) ?? null;

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
            toast("Analysis finished. Views now show the new snapshot.");
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
        ? { repository, runs, latestRun, published, startAnalysis, starting, inventory, architecture, models, trainModels, training, openEvidence: setEvidenceId }
        : null,
    [repository, runs, latestRun, published, startAnalysis, starting, inventory, architecture, models, trainModels, training],
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
  const { repository, latestRun, published } = useRepo();
  const pathname = usePathname();
  const base = `/r/${repository.id}`;
  const { owner, name } = repoName(repository.external_id);
  const running = latestRun && !terminal.has(latestRun.state);

  return (
    <>
      <TopBar
        title={name}
        detail={<>{owner} · <code>{repository.default_branch}</code>{published && <> · snapshot <code>{shortSha(published.snapshot_sha, 8)}</code></>}</>}
        actions={running ? <span className="badge badge-warn">Analyzing {Math.round((latestRun?.progress ?? 0) * 100)}%</span> : null}
      />
      <nav aria-label="Repository views" className="tabs">
        {repoSections.map((section) => {
          const href = section.slug ? `${base}/${section.slug}` : base;
          const current = section.slug ? pathname.startsWith(href) : pathname === base;
          const Icon = section.icon;
          return (
            <Link aria-current={current ? "page" : undefined} className="tab" href={href} key={section.slug}>
              <Icon size={16} />{section.label}
            </Link>
          );
        })}
      </nav>
      <div className="content">
        {runsError && <Notice tone="error" title="Analysis history is unavailable">{runsError}</Notice>}
        {children}
      </div>
    </>
  );
}

/** Wraps a view that needs a published snapshot and explains how to get one when missing. */
export function RequiresSnapshot({ children, what }: { children: React.ReactNode; what: string }) {
  const { published, latestRun, startAnalysis, starting } = useRepo();
  if (published) return <>{children}</>;
  const running = latestRun && !terminal.has(latestRun.state);
  return (
    <div className="panel">
      <Empty
        centered
        icon={<HelixMark size={36} />}
        title={running ? "Analysis in progress" : "No published snapshot yet"}
        action={
          running ? (
            <div className="progress" style={{ width: 220 }}><i style={{ width: `${Math.max(4, (latestRun?.progress ?? 0) * 100)}%` }} /></div>
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
  if (kind === "module") return `module ${value.slice(0, 14)}`;
  if (kind === "evidence") return `source ${value.slice(0, 12)}`;
  if (kind === "file") return value.split("/").slice(-2).join("/");
  return id.slice(0, 20);
}
