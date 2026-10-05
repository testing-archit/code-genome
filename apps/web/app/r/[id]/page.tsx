"use client";

import type { AnalysisRunWithProgress, RepositoryOverview } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { ComponentMap } from "../../../components/component-map";
import { buildBands, GenomeStrip } from "../../../components/genome-strip";
import { AnalysisProgress } from "../../../components/analysis-progress";
import { EvidenceChips, useRepo } from "../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../components/ui";
import { api } from "../../../lib/api";
import { formatTime, relativeTime, shortSha } from "../../../lib/format";
import { useResource } from "../../../lib/use-resource";

const stateLabel = { QUEUED: "Queued", RUNNING: "Running", SUCCEEDED: "Published", FAILED: "Failed" } as const;

const documents = [
  ["ARCHITECTURE.md", "System overview and module dependencies"],
  ["MODULES.md", "Each component's files, symbols, and links"],
  ["DATA_FLOW.md", "Entry points and import flow"],
  ["DEPENDENCIES.md", "External packages and internal links"],
  ["BUSINESS_LOGIC.md", "Purpose, features, domain vocabulary"],
  ["RISK_REPORT.md", "Health, risky files, hotspots"],
] as const;

export default function OverviewPage() {
  const { published, latestRun } = useRepo();
  if (!published) return <AnalysisStatus prominent />;
  return (
    <>
      {latestRun && latestRun.state !== "SUCCEEDED" && <AnalysisStatus />}
      <Hub />
    </>
  );
}

function Hub() {
  const { repository, published, inventory, architecture } = useRepo();
  const sha = published?.snapshot_sha ?? "";
  const overview = useResource(`${repository.id}:overview:${sha}`, () => api.getOverview(repository.id));
  const graph = useResource(`${repository.id}:module-graph:${sha}`, () => api.getModuleGraph(repository.id));
  const bands = useMemo(() => buildBands(architecture.data, inventory.data), [architecture.data, inventory.data]);
  const router = useRouter();
  const base = `/r/${repository.id}`;

  return (
    <>
      <section className="hub-hero" aria-labelledby="hub-title">
        <div className="hub-identity">
          <h1 id="hub-title">{repository.external_id.split("/")[1] ?? repository.external_id}</h1>
          {overview.loading ? (
            <div className="skeleton" style={{ height: 64, maxWidth: 560 }} />
          ) : overview.error ? (
            <Notice tone="error" title="The overview could not be loaded">{overview.error}</Notice>
          ) : (
            <Summary overview={overview.data} />
          )}
        </div>
        {overview.data && <HealthDial overview={overview.data} />}
      </section>

      <div className="questions">
        <WhatItDoes overview={overview.data} />
        <WhatBreaks />
      </div>

      {overview.data && <Figures overview={overview.data} />}

      <div className="grid-2">
        <Panel
          title="High-risk components"
          description={overview.data ? `Mean risk of each component's riskiest files, from ${overview.data.risk_model}.` : undefined}
          actions={<Link className="button button-ghost button-small" href={`${base}/risk`}>All risk</Link>}
          flush
        >
          {overview.loading ? <Loading rows={4} /> : overview.error ? <Notice tone="error">{overview.error}</Notice> : overview.data?.high_risk_modules.length ? (
            <div className="list">
              {overview.data.high_risk_modules.map((item) => (
                <Link className="list-row" href={`${base}/explorer?component=${encodeURIComponent(item.name)}`} key={item.name}>
                  <div className="grow" style={{ minWidth: 0 }}>
                    <code className="truncate" style={{ display: "block" }}>{item.name}</code>
                    <small className="truncate">{item.files} {item.files === 1 ? "file" : "files"}. Riskiest: {item.riskiest.map((path) => path.split("/").pop()).join(", ")}</small>
                  </div>
                  <Meter tone="eosin" value={item.risk} />
                  <span className="score">{Math.round(item.risk * 100)}%</span>
                </Link>
              ))}
            </div>
          ) : <Empty title="No risk scores yet">Risk needs commit history. Analyze a branch with more commits.</Empty>}
        </Panel>

        <Panel
          title="Riskiest files"
          description="Open one to see what a change to it could break."
          actions={<Link className="button button-ghost button-small" href={`${base}/change`}>Change impact</Link>}
          flush
        >
          {overview.loading ? <Loading rows={4} /> : overview.error ? <Notice tone="error">{overview.error}</Notice> : overview.data?.riskiest_files.length ? (
            <div className="list">
              {overview.data.riskiest_files.map((item) => (
                <Link className="list-row" href={`${base}/change?path=${encodeURIComponent(item.path)}`} key={item.path}>
                  <div className="grow" style={{ minWidth: 0 }}>
                    <code className="truncate" style={{ display: "block" }}>{item.path}</code>
                    <small className="truncate">{item.rationale}</small>
                  </div>
                  <Meter tone="eosin" value={item.score} />
                  <span className="score">{Math.round(item.score * 100)}%</span>
                </Link>
              ))}
            </div>
          ) : <Empty title="No risky source files ranked">Risk needs commit history for JS or TS source files.</Empty>}
        </Panel>
      </div>

      {overview.data?.unstable_components && overview.data.unstable_components.length > 0 && (
        <Panel
          title="Likely to become unstable"
          description={`Forecast for the next period from each component's recent sequence of commits and fixes (${overview.data.unstable_components[0].model_version}). Inferred, not a certainty.`}
          actions={<Link className="button button-ghost button-small" href={`${base}/models`}>How it is evaluated</Link>}
          flush
        >
          <div className="list">
            {overview.data.unstable_components.slice(0, 5).map((item) => (
              <div className="list-row" key={item.name}>
                <div className="grow" style={{ minWidth: 0 }}>
                  <code className="truncate" style={{ display: "block" }}>{item.name}</code>
                  <small>{item.files} {item.files === 1 ? "file" : "files"}{item.fixed_last_period ? ", needed a fix last period" : ""}</small>
                  <EvidenceChips ids={item.evidence_ids} limit={2} />
                </div>
                <span className={`badge ${item.band === "high" ? "badge-bad" : item.band === "medium" ? "badge-warn" : ""}`}>{item.band}</span>
                <Meter tone="eosin" value={item.probability} />
                <span className="score">{Math.round(item.probability * 100)}%</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <div className="grid-2">
        <Panel
          title="Architecture"
          description="Components by folder. Arrows point from a component to what it imports."
          actions={<Link className="button button-ghost button-small" href={`${base}/explorer`}>Open explorer</Link>}
        >
          {graph.loading ? <div className="skeleton" style={{ height: 220 }} /> : graph.error ? (
            <Notice tone="error">{graph.error}</Notice>
          ) : graph.data?.nodes.length ? (
            <div className="map-preview">
              <ComponentMap
                compact
                links={graph.data.links}
                nodes={graph.data.nodes}
                onSelect={(name) => router.push(`${base}/explorer?component=${encodeURIComponent(name)}`)}
              />
            </div>
          ) : <Empty title="No JS or TS components">The architecture map covers JavaScript and TypeScript source.</Empty>}
        </Panel>

        <Panel title="Generated documentation" description="Written from the analysed snapshot, with every statement cited." flush>
          <div className="list">
            {documents.map(([name, description]) => (
              <Link className="list-row" href={`${base}/docs?doc=${encodeURIComponent(name)}`} key={name}>
                <div className="grow" style={{ minWidth: 0 }}>
                  <code>{name}</code>
                  <small className="truncate" style={{ display: "block" }}>{description}</small>
                </div>
              </Link>
            ))}
          </div>
        </Panel>
      </div>

      <section className="genome" aria-labelledby="genome-title">
        <div className="genome-head">
          <div>
            <h2 id="genome-title">Repository genome</h2>
            <p>Each band is an analysed file in path order. Colour shows its inferred module; rose bands change most often.</p>
          </div>
          <Link className="button button-secondary button-small" href={`${base}/files`}>Browse files</Link>
        </div>
        {architecture.loading || inventory.loading ? (
          <div className="skeleton" style={{ height: 116, borderRadius: 58 }} />
        ) : architecture.error || inventory.error ? (
          <Notice tone="error">{architecture.error ?? inventory.error}</Notice>
        ) : (
          <GenomeStrip
            architecture={architecture.data}
            bands={bands}
            onSelect={(path) => router.push(`${base}/files?path=${encodeURIComponent(path)}`)}
          />
        )}
      </section>

      <div className="grid-2">
        <Panel title="Recent changes" actions={<Link className="button button-ghost button-small" href={`${base}/history`}>Evolution timeline</Link>} flush>
          {inventory.loading ? <Loading /> : inventory.error ? <Notice tone="error">{inventory.error}</Notice> : inventory.data?.commits.length ? (
            <div>
              {inventory.data.commits.slice(0, 5).map((commit) => (
                <div className="commit" data-merge={commit.parent_shas.length > 1} key={commit.sha}>
                  <span className="commit-dot" />
                  <div style={{ minWidth: 0 }}>
                    <div className="commit-msg">{commit.message.split("\n")[0]}</div>
                    <div className="commit-meta"><span>{commit.author_name}</span><span>{relativeTime(commit.authored_at)}</span></div>
                  </div>
                  <code className="muted">{shortSha(commit.sha, 8)}</code>
                </div>
              ))}
            </div>
          ) : <Empty title="No commits observed">The mirror did not return history for this branch.</Empty>}
        </Panel>
        <Panel title="Contributors" description={overview.data ? `Across the ${overview.data.counts.commits} most recent analysed commits.` : undefined} flush>
          {overview.loading ? <Loading rows={3} /> : overview.error ? <Notice tone="error">{overview.error}</Notice> : overview.data?.contributors.length ? (
            <div className="list">
              {overview.data.contributors.map((person) => (
                <div className="list-row" key={person.name}>
                  <span className="grow truncate">{person.name}</span>
                  <Meter value={person.commits / Math.max(1, overview.data!.counts.commits)} />
                  <span className="score">{person.commits}</span>
                </div>
              ))}
            </div>
          ) : <Empty title="No contributors observed" />}
        </Panel>
      </div>

      <AnalysisStatus />
      {overview.data && <div className="limitations">{overview.data.limitations.map((item) => <span key={item}>{item}</span>)}</div>}
    </>
  );
}

function Summary({ overview }: { overview: RepositoryOverview | null }) {
  if (!overview?.summary) {
    return <p className="hub-summary muted">This repository has no README description. Ask what it does and the answer will cite its code and history.</p>;
  }
  const text = overview.summary.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[*_>#]/g, "").trim();
  return (
    <div className="hub-summary">
      <p>{text.length > 360 ? `${text.slice(0, 360).replace(/\s+\S*$/, "")}…` : text}</p>
      {overview.summary_evidence_id && <EvidenceChips ids={[overview.summary_evidence_id]} />}
    </div>
  );
}

function HealthDial({ overview }: { overview: RepositoryOverview }) {
  const [open, setOpen] = useState(false);
  const { score, band, components } = overview.health;
  const circumference = 2 * Math.PI * 52;
  const tone = band === "Healthy" ? "ok" : band === "Moderate" ? "warn" : "bad";
  return (
    <div className="health" data-tone={tone}>
      <button aria-expanded={open} className="health-dial" onClick={() => setOpen((value) => !value)} type="button">
        <svg aria-hidden="true" viewBox="0 0 120 120">
          <circle className="health-track" cx="60" cy="60" r="52" />
          <circle
            className="health-value"
            cx="60"
            cy="60"
            r="52"
            strokeDasharray={`${(score / 100) * circumference} ${circumference}`}
            transform="rotate(-90 60 60)"
          />
        </svg>
        <span className="health-score"><strong>{score}</strong><small>/100</small></span>
        <span className="health-band">{band}</span>
        <span className="sr-only">Health score {score} out of 100, {band}. Show how it is scored.</span>
      </button>
      <span className="muted small">{open ? "How it is scored" : "Select to see how it is scored"}</span>
      {open && (
        <div className="health-parts">
          {components.map((part) => (
            <div className="health-part" key={part.key} title={part.detail}>
              <div className="health-part-head"><span>{part.label}</span><span>{part.score}/{part.maximum}</span></div>
              <div className="meter" aria-hidden="true"><i style={{ width: `${(part.score / part.maximum) * 100}%` }} /></div>
              <small className="muted">{part.value}</small>
            </div>
          ))}
          <small className="muted">A transparent heuristic ({overview.health.version}), not a measure of correctness.</small>
        </div>
      )}
    </div>
  );
}

function WhatItDoes({ overview }: { overview: RepositoryOverview | null }) {
  const { repository } = useRepo();
  const base = `/r/${repository.id}`;
  return (
    <article className="question-card">
      <h2>What does this system actually do?</h2>
      <p>
        {overview?.summary
          ? "The summary above is quoted from the repository's own README. Ask for a fuller explanation built from its docs, code, and history."
          : "Ask, and the answer will be built from the repository's docs, code, and history, with every claim cited."}
      </p>
      <div className="question-actions">
        <Link className="button button-primary" href={`${base}/ask?q=${encodeURIComponent("What does this project do, and what are its main parts?")}`}>Explain this project</Link>
        <Link className="button button-secondary" href={`${base}/voice`}>Ask by voice</Link>
        <Link className="button button-ghost" href={`${base}/docs?doc=ARCHITECTURE.md`}>Read the architecture doc</Link>
      </div>
    </article>
  );
}

function WhatBreaks() {
  const { repository, inventory } = useRepo();
  const router = useRouter();
  const [path, setPath] = useState("");
  const files = useMemo(
    () => (inventory.data?.files ?? []).filter((file) => file.analyzed).map((file) => file.path),
    [inventory.data],
  );
  const exact = files.includes(path.trim());

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = path.trim();
    if (value) router.push(`/r/${repository.id}/change?path=${encodeURIComponent(value)}`);
  }

  return (
    <article className="question-card">
      <h2>What will break if I change this file?</h2>
      <p>Pick a file to see which files depend on it, how often they change together, and how risky it is. Or paste a whole diff.</p>
      <form className="question-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="impact-file">File to change</label>
        <input
          autoComplete="off"
          className="input"
          id="impact-file"
          list="impact-files"
          maxLength={1000}
          onChange={(event) => setPath(event.target.value)}
          placeholder={files[0] ?? "src/index.ts"}
          value={path}
        />
        <datalist id="impact-files">
          {files.slice(0, 2000).map((file) => <option key={file} value={file} />)}
        </datalist>
        <button className="button button-primary" disabled={!path.trim()} type="submit">Show impact</button>
      </form>
      <div className="question-actions">
        {path.trim() && !exact && files.length > 0 && <small className="muted">Not an exact file path; the check will match the closest file name.</small>}
        <Link className="button button-ghost" href={`/r/${repository.id}/change`}>Check a whole diff instead</Link>
      </div>
    </article>
  );
}

function Figures({ overview }: { overview: RepositoryOverview }) {
  const { counts } = overview;
  const figures: Array<[number, string]> = [
    [counts.files, "files"],
    [counts.source_files, "JS/TS source files"],
    [counts.commits, "commits analysed"],
    [counts.contributors, counts.contributors === 1 ? "contributor" : "contributors"],
    [counts.modules, "inferred modules"],
    [counts.external_packages, "external packages"],
  ];
  return (
    <div className="stats" aria-label="Snapshot figures">
      {figures.map(([value, label]) => (
        <div className="stat" key={label}><strong>{value.toLocaleString()}</strong><span>{label}</span></div>
      ))}
    </div>
  );
}

function AnalysisStatus({ prominent = false }: { prominent?: boolean }) {
  const { repository, runs, latestRun, published, startAnalysis, starting } = useRepo();
  const running = latestRun && (latestRun.state === "QUEUED" || latestRun.state === "RUNNING");
  const stage = !latestRun ? 1 : latestRun.state === "QUEUED" ? 2 : latestRun.state === "RUNNING" ? 3 : latestRun.state === "SUCCEEDED" ? 4 : 2;
  const [showRuns, setShowRuns] = useState(false);

  return (
    <Panel
      title={running ? "Analysis in progress" : published ? "Analysis" : latestRun?.state === "FAILED" ? "The last analysis failed" : "Analyze this repository to begin"}
      description={
        published
          ? <>Views read commit <code>{shortSha(published.snapshot_sha, 12)}</code>, analysed {relativeTime(published.completed_at)}.</>
          : "Analysis pins the branch head, reads its code, docs, and Git history, and builds the genome every view uses."
      }
      actions={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {runs.length > 0 && !prominent && (
            <button aria-expanded={showRuns} className="button button-ghost button-small" onClick={() => setShowRuns((value) => !value)} type="button">
              {showRuns ? "Hide runs" : `All runs (${runs.length})`}
            </button>
          )}
          <button className={`button ${published ? "button-secondary" : "button-primary"}`} disabled={starting || Boolean(running)} onClick={() => void startAnalysis()} type="button">
            {starting ? "Queuing…" : running ? "Analyzing…" : published ? "Analyze again" : "Analyze repository"}
          </button>
        </div>
      }
    >
      {(prominent || running) && (
        <div className="pipeline" aria-label="Analysis pipeline">
          {["Added", "Queued", "Analyzing", "Published"].map((label, index) => (
            <div className="pipeline-step" data-reached={index < stage} key={label}>{label}</div>
          ))}
        </div>
      )}
      {running && (
        <div style={{ marginTop: 14 }}>
          <AnalysisProgress run={latestRun} />
        </div>
      )}
      {latestRun?.state === "FAILED" && (
        <div style={{ marginTop: 14 }}>
          <Notice tone="error" title={latestRun.error_code ?? "Analysis failed"}>
            {latestRun.error_detail ?? "The worker stopped before publishing a snapshot."}{" "}
            {latestRun.error_code?.includes("CREDENTIAL") || latestRun.error_code?.includes("GIT")
              ? <>If the repository is private, <Link href={`/r/${repository.id}/settings`}>add a read-only token</Link>.</>
              : null}
          </Notice>
          {(latestRun as AnalysisRunWithProgress).stage && <div style={{ marginTop: 12 }}><AnalysisProgress run={latestRun} /></div>}
        </div>
      )}
      {latestRun && latestRun.diagnostics.length > 0 && latestRun.state === "SUCCEEDED" && (
        <p className="muted small" style={{ marginTop: prominent ? 12 : 0 }}>{latestRun.diagnostics.join(" ")}</p>
      )}
      {(showRuns || (prominent && runs.length > 0)) && (
        <div className="table-wrap" style={{ marginTop: 14 }}>
          <table className="table">
            <thead><tr><th scope="col">Status</th><th scope="col">Branch</th><th scope="col">Started</th><th scope="col">Snapshot</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td><span className={`badge ${run.state === "SUCCEEDED" ? "badge-ok" : run.state === "FAILED" ? "badge-bad" : "badge-warn"}`}>{stateLabel[run.state]}</span></td>
                  <td>{run.requested_refs.join(", ")}</td>
                  <td>{formatTime(run.started_at ?? run.created_at)}</td>
                  <td>{run.state === "FAILED" ? <span className="muted">{run.error_code}</span> : <code>{shortSha(run.snapshot_sha, 12)}</code>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
