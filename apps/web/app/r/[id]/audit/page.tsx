"use client";

import type { DeliveryReport } from "@code-genome/contracts";
import { FormEvent, useEffect, useState } from "react";

import { DownloadIcon } from "../../../../components/icons";
import { EvidenceChips, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, errorMessage } from "../../../../lib/api";
import { formatDate, relativeTime } from "../../../../lib/format";

const statusStyle: Record<string, { label: string; badge: string }> = {
  VERIFIED: { label: "Supported by repository evidence", badge: "badge-ok" },
  PARTIALLY_VERIFIED: { label: "Partly supported", badge: "badge-warn" },
  NO_SUPPORTING_EVIDENCE: { label: "No supporting evidence found", badge: "badge-bad" },
  EXTERNAL_EVIDENCE_REQUIRED: { label: "Needs CI or deployment evidence", badge: "" },
};

function dateInput(daysAgo: number) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - daysAgo);
  return date.toISOString().slice(0, 10);
}

export default function AuditPage() {
  const { repository } = useRepo();
  const { toast } = useWorkspace();
  const [text, setText] = useState("");
  const [from, setFrom] = useState(() => dateInput(30));
  const [to, setTo] = useState(() => dateInput(0));
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reports, setReports] = useState<DeliveryReport[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listDeliveryReports(repository.id)
      .then((items) => {
        if (!active) return;
        setReports(items);
        setActiveId((current) => current ?? items[0]?.id ?? null);
      })
      .catch(() => {
        if (active) setReports([]);
      });
    return () => {
      active = false;
    };
  }, [repository.id]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const created = await api.createDeliveryReport(repository.id, text, from, to, repository.default_branch);
      const assessed = await api.assessDeliveryReport(created.id);
      setReports((current) => [assessed, ...(current ?? []).filter((item) => item.id !== assessed.id)]);
      setActiveId(assessed.id);
      setText("");
      toast(`Checked ${assessed.claims.length} claims`);
    } catch (caught) {
      setError(errorMessage(caught, "The report could not be checked."));
    } finally {
      setWorking(false);
    }
  }

  const report = reports?.find((item) => item.id === activeId) ?? null;
  const counts = report?.claims.reduce<Record<string, number>>((acc, claim) => {
    const key = claim.assessment?.status ?? "PENDING";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0, 0.9fr) minmax(0, 1.3fr)" }}>
      <div style={{ display: "grid", gap: 20, alignContent: "start" }}>
        <Panel title="Check a delivery report" description="Paste a status update. Each claim is matched against commits and diffs in the date range.">
          <form onSubmit={submit} style={{ display: "grid", gap: 14 }}>
            <label className="field">
              Report text
              <textarea className="textarea" maxLength={100000} onChange={(event) => setText(event.target.value)} placeholder={"Added invoice export.\nFixed the login redirect.\nDeployed to production."} required rows={7} value={text} />
            </label>
            <div className="grid-2" style={{ gap: 12 }}>
              <label className="field">From<input className="input" max={to} onChange={(event) => setFrom(event.target.value)} required type="date" value={from} /></label>
              <label className="field">To<input className="input" min={from} onChange={(event) => setTo(event.target.value)} required type="date" value={to} /></label>
            </div>
            <p className="muted small">Branch <code>{repository.default_branch}</code>. Claims about tests passing or deployments stay unverified until CI or deployment evidence is connected.</p>
            {error && <Notice tone="error">{error}</Notice>}
            <button className="button button-primary" disabled={working || text.trim().length === 0} type="submit">{working ? "Checking claims…" : "Check report"}</button>
          </form>
        </Panel>
        <Panel title="Past checks" flush>
          {reports === null ? <Loading rows={3} /> : reports.length === 0 ? <Empty title="No reports checked yet">Checked reports are kept here with their evidence.</Empty> : (
            <div className="list">
              {reports.map((item) => (
                <button aria-pressed={item.id === activeId} className="list-row" key={item.id} onClick={() => setActiveId(item.id)} type="button">
                  <div className="grow"><span className="truncate" style={{ display: "block" }}>{item.raw_text.split("\n")[0]}</span><small>{item.claims.length} claims · {relativeTime(item.created_at)}</small></div>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title={report ? `${report.claims.length} claims` : "Results"}
        description={report ? `${formatDate(report.scope.from)} to ${formatDate(report.scope.to)} on ${report.scope.branches.join(", ")}` : "Results appear here after a check."}
        actions={report && <button className="button button-secondary button-small" onClick={() => void api.downloadDeliveryReport(report.id).catch((caught: unknown) => toast(errorMessage(caught, "Download failed.")))} type="button"><DownloadIcon size={15} />Markdown</button>}
        flush
      >
        {!report ? (
          <Empty centered title="The exact words stay attached">Every claim keeps its original text, character span, status, evidence, and limits, so the result can be checked by someone else.</Empty>
        ) : (
          <>
            {counts && (
              <div className="chip-row" style={{ padding: "14px 20px", borderBottom: "1px solid var(--rule)" }}>
                {Object.entries(counts).map(([status, count]) => (
                  <span className={`badge ${statusStyle[status]?.badge ?? ""}`} key={status}>{count} {statusStyle[status]?.label.toLowerCase() ?? "pending"}</span>
                ))}
              </div>
            )}
            {report.claims.map((claim) => {
              const style = statusStyle[claim.assessment?.status ?? ""];
              return (
                <article className="claim" key={claim.id}>
                  <div className="claim-top">
                    <div>
                      <div className="claim-text">“{claim.original_text}”</div>
                      <div className="claim-span">Claim {claim.ordinal + 1}, characters {claim.start_offset}–{claim.end_offset}, {claim.claim_type.replaceAll("_", " ").toLowerCase()}</div>
                    </div>
                    <span className={`badge ${style?.badge ?? ""}`}>{style?.label ?? "Pending"}</span>
                  </div>
                  {claim.assessment && (
                    <>
                      <p className="small">{claim.assessment.rationale}</p>
                      <div style={{ display: "flex", gap: 10, alignItems: "center" }}><Meter value={claim.assessment.confidence} /><span className="muted small">{Math.round(claim.assessment.confidence * 100)}% match</span></div>
                      <EvidenceChips ids={claim.assessment.evidence_ids} limit={6} />
                      {claim.assessment.limitations.length > 0 && <div className="limitations">{claim.assessment.limitations.map((item) => <span key={item}>{item}</span>)}</div>}
                    </>
                  )}
                </article>
              );
            })}
            {report.unreported_changes.length > 0 && (
              <div style={{ borderTop: "1px solid var(--rule)" }}>
                <div className="panel-head"><div><h2>Changes the report did not mention</h2><p>Files changed in range with no matching claim, ordered by size of change.</p></div></div>
                <div className="list">
                  {report.unreported_changes.slice(0, 12).map((change) => (
                    <div className="list-row" key={change.path}>
                      <div className="grow"><code className="truncate" style={{ display: "block" }}>{change.path}</code><small>{change.explanation}</small></div>
                      <Meter value={change.materiality} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
