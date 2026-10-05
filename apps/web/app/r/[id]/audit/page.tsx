"use client";

import type { DeliveryReport } from "@code-genome/contracts";
import { FormEvent, useEffect, useState } from "react";

import { DownloadIcon } from "../../../../components/icons";
import { EvidenceChips, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, errorMessage } from "../../../../lib/api";
import { formatScopeDate, localDateInput, relativeTime } from "../../../../lib/format";

const statusStyle: Record<string, { label: string; badge: string }> = {
  VERIFIED: { label: "Supported by repository evidence", badge: "badge-ok" },
  PARTIALLY_VERIFIED: { label: "Partly supported", badge: "badge-warn" },
  NO_SUPPORTING_EVIDENCE: { label: "No supporting evidence found", badge: "badge-bad" },
  EXTERNAL_EVIDENCE_REQUIRED: { label: "Needs CI or deployment evidence", badge: "" },
};

/** A report has been created but its claims have not all been assessed (e.g. assessment failed). */
function unassessed(report: DeliveryReport): boolean {
  return report.claims.some((claim) => !claim.assessment);
}

export default function AuditPage() {
  const { repository } = useRepo();
  const { toast } = useWorkspace();
  const [text, setText] = useState("");
  // Inputs are local calendar days, so early-morning users east of UTC default to today.
  const [from, setFrom] = useState(() => localDateInput(30));
  const [to, setTo] = useState(() => localDateInput(0));
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reports, setReports] = useState<DeliveryReport[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [listNonce, setListNonce] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listDeliveryReports(repository.id)
      .then((items) => {
        if (!active) return;
        setListError(null);
        // Keep locally created reports the list request may not have seen yet.
        setReports((current) => [...(current ?? []).filter((item) => !items.some((next) => next.id === item.id)), ...items]);
        setActiveId((current) => current ?? items[0]?.id ?? null);
      })
      .catch((caught: unknown) => {
        if (active) setListError(errorMessage(caught, "Past checks could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, [repository.id, listNonce]);

  function retryList() {
    setListError(null);
    setListNonce((value) => value + 1);
  }

  function upsert(report: DeliveryReport) {
    setReports((current) => [report, ...(current ?? []).filter((item) => item.id !== report.id)]);
    setActiveId(report.id);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    let created: DeliveryReport;
    try {
      created = await api.createDeliveryReport(repository.id, text, from, to, repository.default_branch);
    } catch (caught) {
      setError(errorMessage(caught, "The report could not be saved."));
      setWorking(false);
      return;
    }
    // The report now exists; keep it even if the check fails so a retry does not create a duplicate.
    upsert(created);
    setText("");
    try {
      const assessed = await api.assessDeliveryReport(created.id);
      upsert(assessed);
      toast(`Checked ${assessed.claims.length} claims`);
    } catch (caught) {
      setError(`The report was saved but its claims were not checked: ${errorMessage(caught, "the check failed.")} Use “Run check again” on the saved report.`);
    } finally {
      setWorking(false);
    }
  }

  async function recheck(reportId: string) {
    setRechecking(reportId);
    setError(null);
    try {
      const assessed = await api.assessDeliveryReport(reportId);
      upsert(assessed);
      toast(`Checked ${assessed.claims.length} claims`);
    } catch (caught) {
      setError(errorMessage(caught, "The claims could not be checked."));
    } finally {
      setRechecking(null);
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
          {listError && (
            <div className="panel-body" style={{ display: "grid", gap: 8, justifyItems: "start" }}>
              <Notice tone="error" title="Past checks could not be loaded">{listError}</Notice>
              <button className="button button-secondary button-small" onClick={retryList} type="button">Try again</button>
            </div>
          )}
          {reports === null ? (listError ? null : <Loading rows={3} />) : reports.length === 0 ? (listError ? null : <Empty title="No reports checked yet">Checked reports are kept here with their evidence.</Empty>) : (
            <div className="list">
              {reports.map((item) => (
                <button aria-pressed={item.id === activeId} className="list-row" key={item.id} onClick={() => setActiveId(item.id)} type="button">
                  <div className="grow"><span className="truncate" style={{ display: "block" }}>{item.raw_text.split("\n")[0]}</span><small>{item.claims.length} claims · {unassessed(item) ? "not checked yet · " : ""}{relativeTime(item.created_at)}</small></div>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title={report ? `${report.claims.length} claims` : "Results"}
        description={report ? `${formatScopeDate(report.scope.from)} to ${formatScopeDate(report.scope.to)} (UTC) on ${report.scope.branches.join(", ")}` : "Results appear here after a check."}
        actions={report && (
          <div style={{ display: "flex", gap: 8 }}>
            {unassessed(report) && (
              <button className="button button-primary button-small" disabled={rechecking === report.id || working} onClick={() => void recheck(report.id)} type="button">
                {rechecking === report.id ? "Checking…" : "Run check again"}
              </button>
            )}
            <button className="button button-secondary button-small" onClick={() => void api.downloadDeliveryReport(report.id).catch((caught: unknown) => toast(errorMessage(caught, "Download failed.")))} type="button"><DownloadIcon size={15} />Markdown</button>
          </div>
        )}
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
