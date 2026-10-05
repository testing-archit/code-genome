"use client";

import type { ChangeImpact, ChangeKind } from "@code-genome/contracts";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useRef, useState } from "react";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Meter, Notice, Panel } from "../../../../components/ui";
import { api, errorMessage } from "../../../../lib/api";
import { shortSha } from "../../../../lib/format";

const changeBadge: Record<ChangeKind, string> = {
  added: "badge-ok",
  modified: "badge-hema",
  deleted: "badge-bad",
  renamed: "badge-warn",
  listed: "",
};

const example = `diff --git a/src/api/client.ts b/src/api/client.ts
--- a/src/api/client.ts
+++ b/src/api/client.ts
@@ -10,1 +10,1 @@
-const timeout = 5000;
+const timeout = 8000;`;

export default function ChangePage() {
  return (
    <RequiresSnapshot what="Change impact">
      <Suspense fallback={null}>
        <ChangeView />
      </Suspense>
    </RequiresSnapshot>
  );
}

function ChangeView() {
  const { repository } = useRepo();
  const params = useSearchParams();
  const requestedPath = params.get("path");
  const [diff, setDiff] = useState("");
  const [paths, setPaths] = useState(requestedPath ?? "");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ChangeImpact | null>(null);

  const listedPaths = paths.split("\n").map((line) => line.trim()).filter(Boolean);
  const tooLarge =
    new Blob([diff]).size > 900_000
      ? "The diff is larger than 900 KB. Check a smaller range of commits, or list the changed paths instead."
      : listedPaths.length > 200
        ? `Checks are limited to 200 paths; ${listedPaths.length} are listed.`
        : null;
  const canSubmit = !working && !tooLarge && (diff.trim().length > 0 || listedPaths.length > 0);

  async function run(diffText: string, pathList: string[]) {
    setWorking(true);
    setError(null);
    try {
      setResult(await api.checkChange(repository.id, diffText, pathList));
    } catch (caught) {
      setError(errorMessage(caught, "The change could not be checked."));
    } finally {
      setWorking(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(diff, listedPaths);
  }

  // Arriving from "What will break if I change this file?" runs the check straight away.
  const ranFor = useRef<string | null>(null);
  useEffect(() => {
    if (!requestedPath || ranFor.current === requestedPath) return;
    ranFor.current = requestedPath;
    setPaths(requestedPath);
    void run("", [requestedPath]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedPath]);

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0, 0.85fr) minmax(0, 1.35fr)" }}>
      <Panel title="What will break?" description="Name a file, list the files a pull request touches, or paste a diff. Nothing is stored.">
        <form onSubmit={submit} style={{ display: "grid", gap: 14 }}>
          <label className="field">
            Unified diff
            <textarea
              className="textarea"
              onChange={(event) => setDiff(event.target.value)}
              placeholder={example}
              rows={12}
              spellCheck={false}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}
              value={diff}
            />
            <small>Output of <code>git diff main...my-branch</code>, or a pull request&apos;s <code>.diff</code>. Up to 200 files.</small>
          </label>
          <label className="field">
            Files you plan to change, one per line
            <textarea className="textarea" onChange={(event) => setPaths(event.target.value)} placeholder="src/billing/invoice.ts" rows={4} spellCheck={false} value={paths} />
          </label>
          {tooLarge && <Notice tone="warn">{tooLarge}</Notice>}
          {error && <Notice tone="error" title="Check failed">{error}</Notice>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="button button-primary" disabled={!canSubmit} type="submit">{working ? "Checking…" : "Check impact"}</button>
            {(diff || paths) && <button className="button button-ghost" disabled={working} onClick={() => { setDiff(""); setPaths(""); setResult(null); setError(null); }} type="button">Clear</button>}
          </div>
        </form>
      </Panel>

      <div style={{ display: "grid", gap: 20, alignContent: "start" }}>
        {!result ? (
          <Panel><Empty title="No change checked yet">Results rank the files this change may affect, using imports and co-change history from the latest snapshot.</Empty></Panel>
        ) : <ChangeResult result={result} />}
      </div>
    </div>
  );
}

function ChangeResult({ result }: { result: ChangeImpact }) {
  const { summary } = result;
  const lead = result.changed[0];
  const band = (score: number) => (score >= 0.6 ? "High" : score >= 0.3 ? "Medium" : "Low");
  return (
    <>
      {lead && (
        <div className="impact-lead" data-tone={lead.risk_score === null ? "none" : band(lead.risk_score).toLowerCase()}>
          <div style={{ minWidth: 0 }}>
            <span className="muted small">{result.changed.length === 1 ? "Changing" : `Changing ${result.changed.length} files, riskiest first`}</span>
            <code className="impact-lead-path">{lead.path}</code>
            {!lead.in_snapshot && <small className="muted">Not in the analysed snapshot. If you meant another file, check the spelling or pick one from the overview.</small>}
          </div>
          {lead.risk_score !== null && (
            <div className="impact-lead-risk">
              <strong>{Math.round(lead.risk_score * 100)}%</strong>
              <span>{band(lead.risk_score)} regression risk</span>
            </div>
          )}
          <div className="impact-lead-risk">
            <strong>{summary.impacted_files}</strong>
            <span>{summary.impacted_files === 1 ? "file may be affected" : "files may be affected"}</span>
          </div>
        </div>
      )}
      <div className="stats" aria-label="Change summary">
        <div className="stat"><strong>{summary.changed_files}</strong><span>files changed ({summary.changed_in_snapshot} known)</span></div>
        <div className="stat"><strong>{summary.impacted_files}</strong><span>may be affected</span></div>
        <div className="stat"><strong>{summary.modules_touched}</strong><span>modules touched</span></div>
        <div className="stat"><strong>{summary.max_risk === null ? "—" : Math.round(summary.max_risk * 100)}</strong><span>highest risk · {summary.high_risk_files} ≥ 60</span></div>
      </div>

      <Panel title="Changed files" description={`Risk from snapshot ${shortSha(result.snapshot_sha)}, highest first.`} flush>
        <div className="list">
          {result.changed.map((item) => (
            <div className="list-row" key={item.path} style={{ alignItems: "flex-start" }}>
              <div className="grow" style={{ display: "grid", gap: 6, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", minWidth: 0 }}>
                  <span className={`badge ${changeBadge[item.change]}`}>{item.change}</span>
                  <code className="truncate">{item.path}</code>
                </div>
                {item.previous_path && <small>renamed from <code>{item.previous_path}</code></small>}
                <small>
                  {item.change === "listed" ? "" : `+${item.additions} −${item.deletions} · `}
                  {item.in_snapshot ? item.risk_rationale ?? "No risk signal for this file." : "Not in the analysed snapshot; no history or graph evidence."}
                </small>
                {item.modules.length > 0 && <div className="chip-row">{item.modules.map((name) => <span className="chip" key={name}>{name}</span>)}</div>}
                <EvidenceChips ids={item.evidence_ids} limit={3} />
              </div>
              {item.risk_score !== null && <><Meter tone="eosin" value={item.risk_score} /><span className="score">{Math.round(item.risk_score * 100)}</span></>}
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Possibly affected" description="One hop through observed imports and repeated co-change, excluding the changed files." flush>
        {result.impacted.length === 0 ? (
          <Empty title="No observed neighbours">No supporting evidence was identified in the selected scope. That does not rule out runtime impact.</Empty>
        ) : (
          <div className="list">
            {result.impacted.map((item) => (
              <div className="list-row" key={item.path} style={{ alignItems: "flex-start" }}>
                <div className="grow" style={{ display: "grid", gap: 6, minWidth: 0 }}>
                  <code className="truncate">{item.path}</code>
                  <small>via {item.via.map((path, index) => <span key={path}>{index > 0 && ", "}<code>{path}</code></span>)}</small>
                  <small>{item.reasons.join("; ")}</small>
                  <EvidenceChips ids={item.evidence_ids} limit={3} />
                  {item.signals && <WhyImpacted signals={item.signals} weighted={item.weighted_score ?? null} />}
                </div>
                <Meter value={item.score} />
                <span className="score">{Math.round(item.score * 100)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {result.modules.length > 0 && (
        <Panel title="Modules touched" description="Module boundaries are inferred." flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Module</th><th scope="col">Changed</th><th scope="col">Affected</th></tr></thead>
              <tbody>
                {result.modules.map((item) => (
                  <tr key={item.name}><td><code>{item.name}</code></td><td>{item.changed_files}</td><td>{item.impacted_files}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <div className="limitations">{result.limitations.map((item) => <span key={item}>{item}</span>)}</div>
    </>
  );
}

const SIGNALS = [
  { key: "dependency", label: "Direct dependency", weight: 0.35, hint: "It imports the changed file, directly or through one more file." },
  { key: "co_change", label: "Changed together", weight: 0.3, hint: "How reliably the two files have changed in the same commits." },
  { key: "proximity", label: "Graph proximity", weight: 0.2, hint: "How close they are in the import graph, up to three hops." },
  { key: "bug_correlation", label: "Shared bug fixes", weight: 0.15, hint: "Share of bug-fix commits on the changed file that also touched this one." },
] as const;

/** The spec's explainable impact score: each signal, its weight, and what it contributed. */
function WhyImpacted({
  signals,
  weighted,
}: {
  signals: NonNullable<NonNullable<ChangeImpact["impacted"][number]["signals"]>>;
  weighted: number | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="why">
      <button aria-expanded={open} className="link-button" onClick={() => setOpen((value) => !value)} type="button">
        {open ? "Hide why" : "Why?"}
      </button>
      {open && (
        <div className="why-body">
          {SIGNALS.map((signal) => {
            const value = signals[signal.key];
            return (
              <div className="why-row" key={signal.key} title={signal.hint}>
                <span>{signal.label}</span>
                <div className="meter" aria-hidden="true"><i style={{ width: `${Math.max(2, value * 100)}%` }} /></div>
                <span className="muted small">{value.toFixed(2)} × {signal.weight}</span>
                <strong>{(value * signal.weight).toFixed(2)}</strong>
              </div>
            );
          })}
          {weighted !== null && (
            <div className="why-row why-total"><span>Weighted impact score</span><span /><span /><strong>{weighted.toFixed(2)}</strong></div>
          )}
          <small className="muted">Signals are normalised to 0–1 and come from the import graph and commit history of the analysed snapshot.</small>
        </div>
      )}
    </div>
  );
}
