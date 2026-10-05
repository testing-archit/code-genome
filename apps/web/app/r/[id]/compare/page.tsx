"use client";

import type { ComparedFile, ComparedImport, SnapshotComparison, SnapshotSummary } from "@code-genome/contracts";
import { FormEvent, useState } from "react";

import { ExportButtons } from "../../../../components/export-buttons";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { formatBytes, formatTime, shortSha } from "../../../../lib/format";
import { api, errorMessage } from "../../../../lib/api";
import { useResource } from "../../../../lib/use-resource";

export default function ComparePage() {
  return (
    <RequiresSnapshot what="Compare">
      <CompareView />
    </RequiresSnapshot>
  );
}

function snapshotLabel(item: SnapshotSummary): string {
  const point = item.as_of ? `as of ${item.as_of.length === 40 ? shortSha(item.as_of) : item.as_of}` : "branch head";
  return `${shortSha(item.commit_sha)} · ${item.refs.join(", ") || "no ref"} · ${point} · analysed ${formatTime(item.published_at)}`;
}

/** Branch-head snapshots first (newest analysis first), then past points (latest point first). */
function ordered(items: SnapshotSummary[]): SnapshotSummary[] {
  const heads = items.filter((item) => !item.as_of);
  const past = items.filter((item) => item.as_of).sort((a, b) => (b.as_of ?? "").localeCompare(a.as_of ?? ""));
  return [...heads, ...past];
}

function PastPointForm() {
  const { repository, runs, retryRuns } = useRepo();
  const { toast } = useWorkspace();
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const running = runs.some((run) => run.state === "QUEUED" || run.state === "RUNNING");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!day) return;
    setBusy(true);
    try {
      await api.createAnalysis(repository.id, repository.default_branch, day);
      toast(`Analysing ${repository.default_branch} as of ${day}.`);
      retryRuns();
    } catch (caught) {
      toast(errorMessage(caught, "The past snapshot could not be queued."));
    } finally {
      setBusy(false);
    }
  }
  const today = new Date().toISOString().slice(0, 10);
  return (
    <form className="row" onSubmit={submit} style={{ gap: 8, flexWrap: "wrap", alignItems: "end" }}>
      <label className="field" style={{ minWidth: 200 }}>
        Analyse {repository.default_branch} as of
        <input className="input" max={today} onChange={(event) => setDay(event.target.value)} required type="date" value={day} />
      </label>
      <button className="button button-secondary" disabled={busy || running || !day} type="submit">
        {busy ? "Queuing…" : running ? "Analysis running…" : "Add past snapshot"}
      </button>
      <span className="muted small">Uses the newest commit on or before that day (UTC) within the fetched history. Current views keep showing the branch head.</span>
    </form>
  );
}

function CompareView() {
  const { repository, published } = useRepo();
  const snapshots = useResource(`${repository.id}:snapshots:${published?.snapshot_sha ?? ""}`, () => api.listSnapshots(repository.id));
  const [base, setBase] = useState<string | null>(null);
  const [head, setHead] = useState<string | null>(null);
  const items = ordered(snapshots.data ?? []);
  const headSha = head ?? items[0]?.commit_sha ?? null;
  const baseSha = base ?? items[1]?.commit_sha ?? null;
  const ready = Boolean(baseSha && headSha && baseSha !== headSha);
  const comparison = useResource(ready ? `${repository.id}:compare:${baseSha}:${headSha}` : null, () =>
    api.compareSnapshots(repository.id, baseSha ?? "", headSha ?? ""),
  );

  if (snapshots.error) return <Notice tone="error" title="Snapshots could not be loaded">{snapshots.error}</Notice>;
  if (!snapshots.data) return <Panel><Loading rows={3} /></Panel>;
  if (items.length < 2) {
    return (
      <Panel>
        <Empty centered title="One snapshot so far" action={<PastPointForm />}>
          Comparison needs two published snapshots. Analyse the branch as it was on an earlier day, analyse again after new commits land, or turn on automatic analysis in Settings.
        </Empty>
      </Panel>
    );
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <Panel
        title="Compare snapshots"
        description="Each snapshot pins a commit. Differences come from their manifests, import graphs, inferred modules, and hotspots."
        actions={<ExportButtons disabled={!comparison.data} kind="comparison" range={ready ? { base: baseSha ?? "", head: headSha ?? "" } : undefined} repositoryId={repository.id} />}
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14 }}>
          <label className="field">
            Base (older)
            <select className="select input" onChange={(event) => setBase(event.target.value)} value={baseSha ?? ""}>
              {items.map((item) => <option key={item.id} value={item.commit_sha}>{snapshotLabel(item)}</option>)}
            </select>
          </label>
          <label className="field">
            Head (newer)
            <select className="select input" onChange={(event) => setHead(event.target.value)} value={headSha ?? ""}>
              {items.map((item) => <option key={item.id} value={item.commit_sha}>{snapshotLabel(item)}</option>)}
            </select>
          </label>
        </div>
        {!ready && <div style={{ marginTop: 12 }}><Notice tone="warn">Choose two different snapshots.</Notice></div>}
        <div style={{ marginTop: 14 }}><PastPointForm /></div>
      </Panel>

      {comparison.error ? <Notice tone="error" title="Comparison failed">{comparison.error}</Notice> : comparison.loading || !comparison.data ? (ready ? <Panel><Loading rows={5} /></Panel> : null) : <ComparisonResult data={comparison.data} />}
    </div>
  );
}

function ComparisonResult({ data }: { data: SnapshotComparison }) {
  const { counts } = data;
  const missing = new Set(data.unavailable);
  const stat = (section: "files" | "imports" | "modules", value: string) => (missing.has(section) ? "—" : value);
  return (
    <>
      {missing.size > 0 && (
        <Notice tone="warn" title={`Not compared: ${data.unavailable.join(", ")}`}>
          At least one of these snapshots has no records of that kind, usually because an earlier version of the analyser produced it. Re-analyze to compare them fully.
        </Notice>
      )}
      <div className="stats" aria-label="Comparison summary">
        <div className="stat"><strong>{stat("files", `+${counts.files_added}`)}</strong><span>files added</span></div>
        <div className="stat"><strong>{stat("files", `−${counts.files_removed}`)}</strong><span>files removed</span></div>
        <div className="stat"><strong>{stat("files", String(counts.files_modified))}</strong><span>files modified</span></div>
        <div className="stat"><strong>{stat("imports", `+${counts.imports_added} / −${counts.imports_removed}`)}</strong><span>imports</span></div>
        <div className="stat"><strong>{stat("modules", String(counts.modules_changed))}</strong><span>modules changed</span></div>
      </div>

      {!missing.has("files") && (
        <div className="split">
          <FileList title="Added files" files={data.files_added} total={counts.files_added} />
          <FileList title="Removed files" files={data.files_removed} total={counts.files_removed} />
        </div>
      )}
      <div className="split">
        {!missing.has("files") && <FileList title="Modified files" files={data.files_modified} total={counts.files_modified} />}
        {!missing.has("hotspots") && <Panel title="Hotspot movement" description="Relative scores within each snapshot; largest moves first." flush>
          {data.hotspots.length === 0 ? <Empty title="No movement">Hotspot scores are the same in both snapshots.</Empty> : (
            <div className="list">
              {data.hotspots.map((item) => (
                <div className="list-row" key={item.path}>
                  <code className="grow truncate">{item.path}</code>
                  <span className="muted small" style={{ fontVariantNumeric: "tabular-nums" }}>{item.base_score?.toFixed(2) ?? "—"} → {item.head_score?.toFixed(2) ?? "—"}</span>
                  <span className={`badge ${item.delta > 0 ? "badge-eosin" : "badge-ok"}`}>{item.delta > 0 ? "+" : ""}{item.delta.toFixed(2)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>}
      </div>
      {!missing.has("imports") && (
        <div className="split">
          <ImportList title="Imports added" items={data.imports_added} total={counts.imports_added} />
          <ImportList title="Imports removed" items={data.imports_removed} total={counts.imports_removed} />
        </div>
      )}

      {!missing.has("modules") && <Panel title="Module changes" description="Module boundaries in both snapshots are inferred." flush>
        {data.modules.length === 0 ? <Empty title="Same modules">Inferred modules hold the same files in both snapshots.</Empty> : (
          <div>
            {data.modules.map((item) => (
              <article className="claim" key={item.name}>
                <div className="claim-top">
                  <h3 className="truncate"><code>{item.name}</code></h3>
                  <span className={`badge ${item.status === "added" ? "badge-ok" : item.status === "removed" ? "badge-bad" : "badge-warn"}`}>{item.status}</span>
                </div>
                {item.added_files.length > 0 && <p className="small">Gained: {item.added_files.slice(0, 8).map((path) => <code key={path} style={{ marginRight: 6 }}>{path}</code>)}{item.added_files.length > 8 && `+${item.added_files.length - 8} more`}</p>}
                {item.removed_files.length > 0 && <p className="small">Lost: {item.removed_files.slice(0, 8).map((path) => <code key={path} style={{ marginRight: 6 }}>{path}</code>)}{item.removed_files.length > 8 && `+${item.removed_files.length - 8} more`}</p>}
              </article>
            ))}
          </div>
        )}
      </Panel>}

      <div className="limitations">{data.limitations.map((item) => <span key={item}>{item}</span>)}</div>
    </>
  );
}

function FileList({ title, files, total }: { title: string; files: ComparedFile[]; total: number }) {
  return (
    <Panel title={title} description={files.length < total ? `Showing ${files.length} of ${total}` : `${total} file${total === 1 ? "" : "s"}`} flush>
      {files.length === 0 ? <Empty title="None" /> : (
        <div className="list" style={{ maxHeight: 360, overflow: "auto" }}>
          {files.map((item) => (
            <div className="list-row" key={item.path}>
              <code className="grow truncate">{item.path}</code>
              <span className="muted small" style={{ fontVariantNumeric: "tabular-nums" }}>{item.size_delta >= 0 ? "+" : "−"}{formatBytes(Math.abs(item.size_delta))}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ImportList({ title, items, total }: { title: string; items: ComparedImport[]; total: number }) {
  return (
    <Panel title={title} description={`${total} import edge${total === 1 ? "" : "s"}`} flush>
      {items.length === 0 ? <Empty title="None" /> : (
        <div className="list" style={{ maxHeight: 360, overflow: "auto" }}>
          {items.map((item) => (
            <div className="list-row" key={`${item.source}->${item.target}`} style={{ alignItems: "flex-start" }}>
              <div className="grow" style={{ display: "grid", gap: 4, minWidth: 0 }}>
                <code className="truncate">{item.source}</code>
                <small className="truncate">→ <code>{item.target}</code></small>
              </div>
              <EvidenceChips ids={[item.evidence_id]} limit={1} />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
