"use client";

import type { AnalysisProgressCounts, AnalysisRun, AnalysisRunWithProgress, AnalysisStage } from "@code-genome/contracts";

type Count = keyof AnalysisProgressCounts;

/* Mirrors the worker's stage order in services/api/.../structural_analysis.py. */
const steps: Array<{ stage: AnalysisStage; label: string; counts: Array<[Count, string]> }> = [
  { stage: "fetching", label: "Fetch mirror", counts: [] },
  { stage: "indexing", label: "Index files", counts: [["files_indexed", "files"], ["source_files", "source files"]] },
  { stage: "mining_history", label: "Mine history", counts: [["commits_mined", "commits"]] },
  { stage: "evolution", label: "Co-change and modules", counts: [["co_change_pairs", "co-change pairs"], ["modules_discovered", "modules"]] },
  { stage: "tracing_bugs", label: "Trace bugs", counts: [["fix_commits", "fix commits"], ["bug_links_traced", "bug links"]] },
  { stage: "parsing", label: "Parse code", counts: [["files_parsed", "files parsed"], ["dependencies_mapped", "dependencies"], ["call_edges", "calls"]] },
  { stage: "publishing", label: "Publish snapshot", counts: [["knowledge_chunks", "knowledge excerpts"]] },
];

/** Stage-by-stage progress with live counts; falls back to a bar for runs without a stage. */
export function AnalysisProgress({ run, compact = false }: { run: AnalysisRun; compact?: boolean }) {
  const { stage, progress_counts: counts = {} } = run as AnalysisRunWithProgress;
  const current = stage ? steps.findIndex((step) => step.stage === stage) : -1;
  const failed = run.state === "FAILED";
  const done = run.state === "SUCCEEDED" || stage === "complete";
  const latest = run.diagnostics.at(-1);

  if (current === -1 && !done) {
    return (
      <div style={{ display: "grid", gap: 6 }}>
        <div className="progress" aria-label="Analysis progress"><i style={{ width: `${Math.max(4, run.progress * 100)}%` }} /></div>
        <span className="muted small">{run.state === "QUEUED" ? "Waiting for a worker…" : `${Math.round(run.progress * 100)}%`}</span>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <ol aria-label="Analysis stages" className={`stage-list ${compact ? "compact" : ""}`}>
        {steps.map((step, index) => {
          const state = done || index < current ? "done" : index === current ? (failed ? "failed" : "active") : "pending";
          const figures = step.counts.filter(([key]) => counts[key] !== undefined);
          return (
            <li aria-current={state === "active" ? "step" : undefined} className="stage" data-state={state} key={step.stage}>
              <span aria-hidden="true" className="stage-dot" />
              <span className="stage-label">{step.label}</span>
              {!compact && figures.length > 0 && (
                <span className="stage-counts">{figures.map(([key, label]) => `${(counts[key] ?? 0).toLocaleString()} ${label}`).join(" · ")}</span>
              )}
              <span className="sr-only">{state === "done" ? "done" : state === "active" ? "in progress" : state === "failed" ? "failed" : "pending"}</span>
            </li>
          );
        })}
      </ol>
      {!done && latest && <p aria-live="polite" className="muted small">{latest}</p>}
    </div>
  );
}
