"use client";

import type { ChangeImpact, ChangeKind } from "@code-genome/contracts";
import { FormEvent, useState } from "react";

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
    <RequiresSnapshot what="Change check">
      <ChangeView />
    </RequiresSnapshot>
  );
}

function ChangeView() {
  const { repository } = useRepo();
  const [diff, setDiff] = useState("");
  const [paths, setPaths] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ChangeImpact | null>(null);

  const listedPaths = paths.split("\n").map((line) => line.trim()).filter(Boolean);
  const canSubmit = !working && (diff.trim().length > 0 || listedPaths.length > 0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      setResult(await api.checkChange(repository.id, diff, listedPaths));
    } catch (caught) {
      setError(errorMessage(caught, "The change could not be checked."));
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0, 0.85fr) minmax(0, 1.35fr)" }}>
      <Panel title="Check a proposed change" description="Paste a diff or list the files a pull request touches. Nothing is stored.">
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
            Or file paths, one per line
            <textarea className="textarea" onChange={(event) => setPaths(event.target.value)} placeholder="src/billing/invoice.ts" rows={4} spellCheck={false} value={paths} />
          </label>
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
  return (
    <>
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
