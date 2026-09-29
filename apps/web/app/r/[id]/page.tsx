"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { buildBands, GenomeStrip } from "../../../components/genome-strip";
import { SendIcon } from "../../../components/icons";
import { useRepo } from "../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../components/ui";
import { formatTime, relativeTime, shortSha } from "../../../lib/format";

const stateLabel = { QUEUED: "Queued", RUNNING: "Running", SUCCEEDED: "Published", FAILED: "Failed" } as const;

export default function OverviewPage() {
  const { repository, runs, latestRun, published, startAnalysis, starting, inventory, architecture } = useRepo();
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const bands = useMemo(() => buildBands(architecture.data, inventory.data), [architecture.data, inventory.data]);
  const running = latestRun && (latestRun.state === "QUEUED" || latestRun.state === "RUNNING");
  const stage = !latestRun ? 1 : latestRun.state === "QUEUED" ? 2 : latestRun.state === "RUNNING" ? 3 : latestRun.state === "SUCCEEDED" ? 4 : 2;

  function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (question.trim().length > 1) router.push(`/r/${repository.id}/ask?q=${encodeURIComponent(question.trim())}`);
  }

  return (
    <>
      <Panel
        title={running ? "Analysis in progress" : published ? "Snapshot published" : latestRun?.state === "FAILED" ? "The last analysis failed" : "Not analyzed yet"}
        description={
          published
            ? <>Views read from commit <code>{shortSha(published.snapshot_sha, 12)}</code>, analyzed {relativeTime(published.completed_at)} with {published.version}.</>
            : "Analysis pins the branch head, extracts JS and TS structure, and reads Git history."
        }
        actions={
          <button className={`button ${published ? "button-secondary" : "button-primary"}`} disabled={starting || Boolean(running)} onClick={() => void startAnalysis()} type="button">
            {starting ? "Queuing…" : running ? "Analyzing…" : published ? "Analyze again" : "Analyze repository"}
          </button>
        }
      >
        <div className="pipeline" aria-label="Analysis pipeline">
          {["Added", "Queued", "Analyzing", "Published"].map((label, index) => (
            <div className="pipeline-step" data-reached={index < stage} key={label}>{label}</div>
          ))}
        </div>
        {running && (
          <div className="progress" style={{ marginTop: 14 }} aria-label="Analysis progress">
            <i style={{ width: `${Math.max(4, latestRun.progress * 100)}%` }} />
          </div>
        )}
        {latestRun?.state === "FAILED" && (
          <div style={{ marginTop: 14 }}>
            <Notice tone="error" title={latestRun.error_code ?? "Analysis failed"}>
              {latestRun.error_detail ?? "The worker stopped before publishing a snapshot."}{" "}
              {latestRun.error_code?.includes("AUTH") || latestRun.error_detail?.toLowerCase().includes("auth")
                ? <>If the repository is private, <Link href={`/r/${repository.id}/settings`}>add a read-only token</Link>.</>
                : null}
            </Notice>
          </div>
        )}
        {latestRun && latestRun.diagnostics.length > 0 && latestRun.state !== "FAILED" && (
          <p className="muted small" style={{ marginTop: 12 }}>{latestRun.diagnostics[0]}</p>
        )}
      </Panel>

      {published && (
        <section className="genome" aria-labelledby="genome-title">
          <div className="genome-head">
            <div>
              <h2 id="genome-title">Repository genome</h2>
              <p>Each band is an analyzed file, in path order. Colour shows the inferred module; rose bands change most often.</p>
            </div>
            <Link className="button button-secondary button-small" href={`/r/${repository.id}/files`}>Browse files</Link>
          </div>
          {architecture.loading || inventory.loading ? (
            <div className="skeleton" style={{ height: 116, borderRadius: 58 }} />
          ) : (
            <GenomeStrip
              architecture={architecture.data}
              bands={bands}
              onSelect={(path) => router.push(`/r/${repository.id}/files?path=${encodeURIComponent(path)}`)}
            />
          )}
        </section>
      )}

      {published && (
        <div className="stats" aria-label="Snapshot figures">
          <div className="stat"><strong>{inventory.data?.files.filter((file) => file.analyzed).length ?? "—"}</strong><span>Analyzed files</span></div>
          <div className="stat"><strong>{inventory.data?.files.length ?? "—"}</strong><span>Files in manifest</span></div>
          <div className="stat"><strong>{inventory.data?.commits.length ?? "—"}</strong><span>Commits read</span></div>
          <div className="stat"><strong>{architecture.data?.modules.length ?? "—"}</strong><span>Inferred modules</span></div>
          <div className="stat"><strong>{architecture.data?.co_changes.length ?? "—"}</strong><span>Co-change pairs</span></div>
        </div>
      )}

      {published && (
        <div className="grid-2">
          <Panel title="Ask about this repository" description="Answers cite the modules, hotspots, and commits they use.">
            <form onSubmit={ask} style={{ display: "grid", gap: 12 }}>
              <div className="composer-box">
                <label className="sr-only" htmlFor="overview-question">Question</label>
                <textarea id="overview-question" onChange={(event) => setQuestion(event.target.value)} placeholder="What changed recently? · Billing module kahan hai?" rows={1} value={question} />
                <button aria-label="Ask" className="button button-primary" type="submit"><SendIcon size={16} /></button>
              </div>
              <div className="suggestions">
                {["What changed recently?", "Which files change most often?", "Invoice export kahan implement hua hai?"].map((item) => (
                  <Link className="suggestion" href={`/r/${repository.id}/ask?q=${encodeURIComponent(item)}`} key={item}>{item}</Link>
                ))}
              </div>
              <Link className="button button-secondary" href={`/r/${repository.id}/voice`}>Talk to the voice agent instead</Link>
            </form>
          </Panel>

          <Panel title="Change hotspots" description="Relative to this repository, from commit frequency and churn." actions={<Link className="button button-ghost button-small" href={`/r/${repository.id}/risk`}>Risk and impact</Link>} flush>
            {architecture.loading ? <Loading /> : architecture.data?.hotspots.length ? (
              <div className="list">
                {architecture.data.hotspots.slice(0, 6).map((hotspot) => (
                  <Link className="list-row" href={`/r/${repository.id}/files?path=${encodeURIComponent(hotspot.path)}`} key={hotspot.path}>
                    <div className="grow"><code className="truncate" style={{ display: "block" }}>{hotspot.path}</code><small>{hotspot.commit_count} commits · {hotspot.churn} lines churned</small></div>
                    <Meter tone="eosin" value={hotspot.score} />
                    <span className="score">{Math.round(hotspot.score * 100)}</span>
                  </Link>
                ))}
              </div>
            ) : <Empty title="No hotspots">Not enough history to rank files yet.</Empty>}
          </Panel>
        </div>
      )}

      {published && (
        <Panel title="Recent commits" actions={<Link className="button button-ghost button-small" href={`/r/${repository.id}/history`}>Full history</Link>} flush>
          {inventory.loading ? <Loading /> : inventory.data?.commits.length ? (
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
      )}

      <Panel title="Analysis runs" description="Each run is kept. Views always read the newest published one." flush>
        {runs.length === 0 ? (
          <Empty title="No runs yet">Start an analysis to create the first snapshot.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Status</th><th>Run</th><th>Branch</th><th>Started</th><th>Snapshot</th></tr></thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td><span className={`badge ${run.state === "SUCCEEDED" ? "badge-ok" : run.state === "FAILED" ? "badge-bad" : "badge-warn"}`}>{stateLabel[run.state]}</span></td>
                    <td><code>{run.id.slice(0, 14)}</code></td>
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
    </>
  );
}
