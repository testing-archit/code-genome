"use client";

import type { BugHistory, BugIntroduction } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel, SearchField } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { formatTime, relativeTime, shortSha } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

const NO_EVIDENCE = "No supporting evidence was identified in the selected scope.";

export default function BugsPage() {
  return (
    <RequiresSnapshot what="Bug history">
      <Suspense fallback={<Panel><Loading rows={6} /></Panel>}>
        <BugsView />
      </Suspense>
    </RequiresSnapshot>
  );
}

function BugsView() {
  const { repository, published } = useRepo();
  const router = useRouter();
  const params = useSearchParams();
  const path = params.get("path");
  const sha = published?.snapshot_sha ?? "";
  const bugs = useResource(`${repository.id}:bugs:${sha}:${path ?? ""}`, () => api.getBugs(repository.id, path, 200));
  const [query, setQuery] = useState("");

  const setPath = (value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set("path", value);
    else next.delete("path");
    router.replace(`/r/${repository.id}/bugs${next.size ? `?${next}` : ""}`);
  };

  const files = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const items = bugs.data?.files ?? [];
    return needle ? items.filter((item) => item.path.toLowerCase().includes(needle)) : items;
  }, [bugs.data, query]);

  if (bugs.error) return <Notice tone="error" title="Bug history could not be loaded">{bugs.error}</Notice>;
  if (!bugs.data) return <Panel><Loading rows={6} /></Panel>;
  const data: BugHistory = bugs.data;
  const base = repository.clone_url.replace(/\.git$/, "");

  return (
    <>
      <Notice tone="warn" title="Candidates, not proof">
        Bug-introducing commits are traced heuristically ({data.analysis_version}): lines removed by a fix are blamed back to the commit that last changed them. Refactors, moved code and fixes that only add lines distort this. Fix commits are chosen by a rule: {data.fix_rule}
      </Notice>

      <div className="stats">
        <div className="stat"><strong>{data.counts.fix_commits}</strong><span>fix commits{path ? " touching this file" : ""}</span></div>
        <div className="stat"><strong>{data.counts.bug_links}</strong><span>candidate bug links</span></div>
        <div className="stat"><strong>{data.counts.files}</strong><span>files with bug history</span></div>
        <div className="stat"><strong className="mono" style={{ fontSize: 18 }}>{shortSha(data.snapshot_sha, 8)}</strong><span>snapshot</span></div>
      </div>

      {path && (
        <div className="chip-row" style={{ alignItems: "center" }}>
          <span className="muted small">Filtered to</span>
          <code>{path}</code>
          <button className="button button-secondary" onClick={() => setPath(null)} type="button">Show all files</button>
          <Link className="button button-secondary" href={`/r/${repository.id}/files?path=${encodeURIComponent(path)}`}>Open in files</Link>
        </div>
      )}

      <div className="split" style={{ "--split-columns": "minmax(0, 0.8fr) minmax(0, 1.4fr)" } as React.CSSProperties}>
        <Panel
          title="Files by bug history"
          description="How often each file was touched by a fix, and by commits that likely introduced a bug."
          actions={<div style={{ width: 200 }}><SearchField label="Filter files" onChange={setQuery} placeholder="Filter files" value={query} /></div>}
          flush
        >
          {files.length === 0 ? (
            <Empty title="No files">{NO_EVIDENCE}</Empty>
          ) : (
            <div className="list" style={{ maxHeight: 560, overflow: "auto" }}>
              {files.slice(0, 200).map((file) => (
                <button aria-pressed={file.path === path} className="list-row" key={file.path} onClick={() => setPath(file.path === path ? null : file.path)} type="button">
                  <span className="grow" style={{ minWidth: 0 }}>
                    <code className="truncate" style={{ display: "block" }}>{file.path}</code>
                    <small>{file.fix_commits} fixes · {file.introducing_commits} likely introducing commits · {file.lines} blamed lines</small>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Fixes and their likely origins" description="Newest fixes first. Each candidate shows the blamed lines and why its confidence is lowered, if it is." flush>
          {data.fixes.length === 0 ? (
            <Empty title="No fix commits traced">{NO_EVIDENCE}</Empty>
          ) : (
            <div>
              {data.fixes.map((fix) => (
                <div className="bug-fix" key={fix.fix_sha}>
                  <div className="row" style={{ gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                    <span className="badge badge-ok">fix</span>
                    <strong className="grow" style={{ minWidth: 0 }}>{fix.subject ?? "(no subject)"}</strong>
                    <a className="mono muted small" href={`${base}/commit/${fix.fix_sha}`} rel="noreferrer" target="_blank">{shortSha(fix.fix_sha, 8)}</a>
                  </div>
                  <div className="muted small">
                    {fix.author ?? "Unknown author"}
                    {fix.authored_at && <> · <span title={formatTime(fix.authored_at)}>{relativeTime(fix.authored_at)}</span></>}
                    {" "}· {fix.files.length} file{fix.files.length === 1 ? "" : "s"}
                  </div>
                  {fix.introducing.length === 0 ? (
                    <p className="muted small">No introducing commit could be traced (the fix may only add lines, or its history is outside the analysed window).</p>
                  ) : (
                    <div style={{ display: "grid", gap: 8 }}>
                      {fix.introducing.slice(0, 6).map((link) => <Introduction base={base} key={`${link.introducing_sha}:${link.path}`} link={link} />)}
                      {fix.introducing.length > 6 && <p className="muted small">+{fix.introducing.length - 6} more candidates</p>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {data.limitations.length > 0 && (
        <Panel title="Limits of this view">
          <ul className="muted small" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {data.limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Panel>
      )}
    </>
  );
}

function Introduction({ link, base }: { link: BugIntroduction; base: string }) {
  const ranges = link.evidence.blamed_ranges.map(([start, end]) => (start === end ? `${start}` : `${start}–${end}`)).join(", ");
  const tone = link.confidence >= 0.6 ? "badge-hema" : "badge-warn";
  return (
    <div className="bug-link">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="muted small">likely introduced in</span>
        <a className="mono small" href={`${base}/commit/${link.introducing_sha}`} rel="noreferrer" target="_blank">{shortSha(link.introducing_sha, 8)}</a>
        <span className="truncate grow small" style={{ minWidth: 0 }}>{link.subject ?? ""}</span>
        <span className={`badge ${tone}`} title="Heuristic confidence, not a probability of guilt">{Math.round(link.confidence * 100)}% confidence</span>
      </div>
      <div className="muted small">
        <code>{link.path}</code> · {link.lines} line{link.lines === 1 ? "" : "s"}{ranges ? ` (parent lines ${ranges})` : ""}
        {link.author && <> · {link.author}</>}
      </div>
      {(link.bulk_commit || link.shallow_boundary) && (
        <div className="chip-row">
          {link.bulk_commit && <span className="badge badge-warn" title="The commit touched very many files or lines, so it is often a reformat or initial import">bulk commit: lower confidence</span>}
          {link.shallow_boundary && <span className="badge badge-warn" title="Blame reached the edge of the fetched history, so the true origin may be older">history boundary: lower confidence</span>}
        </div>
      )}
      <EvidenceChips ids={[link.evidence_id, `commit:${link.introducing_sha}`]} limit={2} />
    </div>
  );
}
