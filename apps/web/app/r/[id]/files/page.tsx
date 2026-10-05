"use client";

import type { RepositoryInventory } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";

import { FileIcon, FilesIcon } from "../../../../components/icons";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel, SearchField } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { formatBytes, relativeTime, shortSha } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

type FileEntry = RepositoryInventory["files"][number];
type TreeNode = { name: string; path: string; children: Map<string, TreeNode>; file: FileEntry | null; size: number; count: number };

function ancestors(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

/** Link to a file at a commit on the forge; each segment is encoded so `#`, `?` and spaces survive. */
function sourceUrl(base: string, sha: string, path: string): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  // Markdown renders by default; `?plain=1` shows the raw text so line anchors resolve.
  return `${base}/blob/${encodeURIComponent(sha)}/${encoded}${/\.(md|mdx|markdown)$/i.test(path) ? "?plain=1" : ""}`;
}

function buildTree(files: FileEntry[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map(), file: null, size: 0, count: 0 };
  for (const file of files) {
    let node = root;
    const parts = file.path.split("/");
    parts.forEach((part, index) => {
      node.size += file.size;
      node.count += 1;
      const path = parts.slice(0, index + 1).join("/");
      let child = node.children.get(part);
      if (!child) {
        child = { name: part, path, children: new Map(), file: null, size: 0, count: 0 };
        node.children.set(part, child);
      }
      node = child;
    });
    node.file = file;
    node.size = file.size;
    node.count = 1;
  }
  return root;
}

export default function FilesPage() {
  return (
    <RequiresSnapshot what="The file explorer">
      <Suspense fallback={null}>
        <FilesView />
      </Suspense>
    </RequiresSnapshot>
  );
}

function FilesView() {
  const { repository, inventory } = useRepo();
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedPath = searchParams.get("path");
  const [query, setQuery] = useState("");
  const [analyzedOnly, setAnalyzedOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(selectedPath ? ancestors(selectedPath) : []));
  // Reveal a file selected by navigation (e.g. a `?path=` link) while this page is already open.
  const [revealedPath, setRevealedPath] = useState(selectedPath);
  if (selectedPath !== revealedPath) {
    setRevealedPath(selectedPath);
    if (selectedPath && ancestors(selectedPath).some((path) => !expanded.has(path))) {
      setExpanded((current) => new Set([...current, ...ancestors(selectedPath)]));
    }
  }
  const treeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selectedPath) return;
    const row = treeRef.current?.querySelector<HTMLElement>(`[data-path="${CSS.escape(selectedPath)}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedPath, inventory.data]);

  const files = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (inventory.data?.files ?? []).filter((file) => (!analyzedOnly || file.analyzed) && (!needle || file.path.toLowerCase().includes(needle)));
  }, [inventory.data, query, analyzedOnly]);
  const tree = useMemo(() => buildTree(files), [files]);
  const searching = query.trim().length > 0;

  function select(path: string) {
    router.replace(`/r/${repository.id}/files?path=${encodeURIComponent(path)}`, { scroll: false });
  }

  function toggle(path: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function renderNode(node: TreeNode, depth: number): React.ReactNode[] {
    const children = [...node.children.values()].sort((a, b) => Number(Boolean(a.file)) - Number(Boolean(b.file)) || a.name.localeCompare(b.name));
    return children.flatMap((child) => {
      const style = { "--depth": depth } as React.CSSProperties;
      if (child.file) {
        return [
          <button aria-pressed={selectedPath === child.path} className="tree-row" data-path={child.path} key={child.path} onClick={() => select(child.path)} style={style} type="button">
            <span className="caret" />
            <FileIcon size={15} />
            <span className="truncate">{child.name}</span>
            {child.file.analyzed && <span className="dot-analyzed" title="Analyzed" />}
            <span className="size">{formatBytes(child.size)}</span>
          </button>,
        ];
      }
      const open = searching || expanded.has(child.path);
      return [
        <button aria-expanded={open} className="tree-row" key={child.path} onClick={() => toggle(child.path)} style={style} type="button">
          <span className="caret">›</span>
          <FilesIcon size={15} />
          <span className="truncate">{child.name}</span>
          <span className="size">{child.count}</span>
        </button>,
        ...(open ? renderNode(child, depth + 1) : []),
      ];
    });
  }

  if (inventory.error) return <Notice tone="error" title="Files could not be loaded">{inventory.error}</Notice>;

  return (
    <div className="split" style={{ "--split-columns": "minmax(0, 1fr) minmax(0, 1.1fr)" } as React.CSSProperties}>
      <Panel
        title="Files"
        description={inventory.data ? `${inventory.data.files.length} in the manifest, ${inventory.data.files.filter((f) => f.analyzed).length} analyzed` : undefined}
        flush
      >
        {inventory.data && inventory.data.limitations.length > 0 && (
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--rule)" }}>
            <Notice tone="warn" title="Inventory is truncated">{inventory.data.limitations.join(" ")}</Notice>
          </div>
        )}
        <div style={{ display: "flex", gap: 8, padding: "12px 16px", borderBottom: "1px solid var(--rule)", flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 180 }}><SearchField label="Filter files" onChange={setQuery} placeholder="Filter by path" value={query} /></div>
          <div className="segmented" role="group" aria-label="File filter">
            <button aria-pressed={!analyzedOnly} onClick={() => setAnalyzedOnly(false)} type="button">All</button>
            <button aria-pressed={analyzedOnly} onClick={() => setAnalyzedOnly(true)} type="button">Analyzed</button>
          </div>
        </div>
        {inventory.loading ? <Loading rows={8} height={24} /> : files.length === 0 ? (
          <Empty title="No files match">Try a shorter path fragment.</Empty>
        ) : (
          <div className="tree" role="tree" aria-label="Repository files" ref={treeRef}>{renderNode(tree, 0)}</div>
        )}
      </Panel>
      {selectedPath ? <FileDetail key={selectedPath} path={selectedPath} /> : (
        <Panel><Empty title="Pick a file">See its module, change risk, and which files are likely to be affected when it changes.</Empty></Panel>
      )}
    </div>
  );
}

function FileDetail({ path }: { path: string }) {
  const { repository, inventory, architecture, published } = useRepo();
  const sha = published?.snapshot_sha ?? "";
  const risk = useResource(`${repository.id}:risk:${sha}`, () => api.getRisk(repository.id));
  const impact = useResource(`${repository.id}:impact:${sha}:${path}`, () => api.getImpact(repository.id, path));
  const analyzed = inventory.data?.files.find((item) => item.path === path)?.analyzed ?? false;
  // The genome's focus view always includes the focused file with its code metrics.
  const genome = useResource(analyzed ? `${repository.id}:file-metrics:${sha}:${path}` : null, () => api.getGenome(repository.id, path, 10));
  const bugs = useResource(`${repository.id}:bugs:${sha}:${path}`, () => api.getBugs(repository.id, path, 20));
  const metrics = genome.data?.nodes.find((node) => node.id === `file:${path}`)?.properties as { loc?: number | null; complexity?: number | null; functions?: number | null } | undefined;
  const file = inventory.data?.files.find((item) => item.path === path) ?? null;
  const owningModule = architecture.data?.modules.find((item) => item.file_paths.includes(path)) ?? null;
  const hotspot = architecture.data?.hotspots.find((item) => item.path === path) ?? null;
  const score = risk.data?.scores.find((item) => item.path === path) ?? null;
  const pairs = architecture.data?.co_changes.filter((item) => item.left_path === path || item.right_path === path) ?? [];
  const base = repository.clone_url.replace(/\.git$/, "");

  return (
    <Panel
      title={path.split("/").pop()}
      description={<code style={{ overflowWrap: "anywhere" }}>{path}</code>}
      actions={<a className="button button-secondary button-small" href={sourceUrl(base, sha, path)} rel="noreferrer" target="_blank">View source</a>}
    >
      <div style={{ display: "grid", gap: 20 }}>
        <dl className="kv">
          <dt>Size</dt><dd>{file ? formatBytes(file.size) : "—"}</dd>
          <dt>Blob</dt><dd><code>{shortSha(file?.blob_sha, 12)}</code></dd>
          <dt>Analyzed</dt><dd>{file?.analyzed ? "Yes, structure extracted" : "No, outside JS/TS scope or limits"}</dd>
          <dt>Module</dt><dd>{owningModule ? <>{owningModule.name} <span className="muted">({Math.round(owningModule.confidence * 100)}% confidence, inferred)</span></> : "None inferred"}</dd>
          <dt>History</dt><dd>{hotspot ? `${hotspot.commit_count} commits, ${hotspot.churn} lines churned` : "No hotspot history recorded"}</dd>
          {analyzed && (
            <>
              <dt>Code metrics</dt>
              <dd>
                {genome.loading ? <span className="muted">Loading…</span> : genome.error ? <span className="muted">Unavailable: {genome.error}</span> : metrics && metrics.loc != null ? (
                  <>{metrics.loc.toLocaleString()} lines of code · complexity {metrics.complexity ?? "—"} · {metrics.functions ?? 0} functions</>
                ) : <span className="muted">Not recorded for this snapshot. Re-analyze to compute them.</span>}
              </dd>
            </>
          )}
        </dl>

        <Link className="button button-secondary" href={`/r/${repository.id}/ask?q=${encodeURIComponent(`Why is ${path} risky?`)}`} style={{ justifySelf: "start" }}>Why is this risky?</Link>

        <div>
          <h3 style={{ marginBottom: 8 }}>Change risk</h3>
          {risk.loading ? <div className="skeleton" style={{ height: 40 }} /> : risk.error ? (
            <div style={{ display: "grid", gap: 8, justifyItems: "start" }}>
              <Notice tone="error" title="Change risk could not be loaded">{risk.error}</Notice>
              <button className="button button-secondary button-small" onClick={risk.reload} type="button">Try again</button>
            </div>
          ) : score ? (
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}><Meter tone="eosin" value={score.score} /><strong>{Math.round(score.score * 100)}</strong><span className="muted small">relative to other files</span></div>
              <p className="small">{score.rationale}</p>
              <EvidenceChips ids={score.evidence_ids} limit={5} />
            </div>
          ) : <p className="muted small">Not ranked. Risk needs change history for this file.</p>}
        </div>

        <div>
          <h3 style={{ marginBottom: 8 }}>Likely affected when this changes</h3>
          {impact.loading ? <div className="skeleton" style={{ height: 60 }} /> : impact.error ? <Notice tone="error">{impact.error}</Notice> : impact.data?.impacted.length ? (
            <div className="list" style={{ margin: "0 -20px" }}>
              {impact.data.impacted.slice(0, 10).map((item) => (
                <div className="list-row" key={item.path}>
                  <div className="grow"><code className="truncate" style={{ display: "block" }}>{item.path}</code><small>{item.reasons.join(", ")}</small></div>
                  <Meter value={item.score} />
                  <span className="score">{Math.round(item.score * 100)}</span>
                </div>
              ))}
            </div>
          ) : <p className="muted small">No one-hop import or repeated co-change was observed. That does not rule out runtime impact.</p>}
        </div>

        <div>
          <h3 style={{ marginBottom: 8 }}>Bug history <span className="badge badge-warn">heuristic</span></h3>
          {bugs.loading ? <div className="skeleton" style={{ height: 40 }} /> : bugs.error ? <Notice tone="error">{bugs.error}</Notice> : bugs.data && bugs.data.fixes.length > 0 ? (
            <div style={{ display: "grid", gap: 8 }}>
              <p className="small">
                Touched by {bugs.data.counts.fix_commits} fix commit{bugs.data.counts.fix_commits === 1 ? "" : "s"}, with {bugs.data.counts.bug_links} candidate bug-introducing link{bugs.data.counts.bug_links === 1 ? "" : "s"} (SZZ-lite candidates, not proof).
              </p>
              {bugs.data.fixes.slice(0, 3).map((fix) => (
                <div className="small" key={fix.fix_sha}>
                  <span className="badge badge-ok">fix</span> {fix.subject ?? shortSha(fix.fix_sha, 8)}{" "}
                  <span className="muted">{fix.authored_at ? relativeTime(fix.authored_at) : ""}{fix.introducing.length ? ` · likely from ${fix.introducing.slice(0, 2).map((item) => shortSha(item.introducing_sha, 7)).join(", ")}` : ""}</span>
                </div>
              ))}
              <Link className="small" href={`/r/${repository.id}/bugs?path=${encodeURIComponent(path)}`}>Full bug history for this file</Link>
            </div>
          ) : <p className="muted small">No supporting evidence was identified in the selected scope.</p>}
        </div>

        {pairs.length > 0 && (
          <div>
            <h3 style={{ marginBottom: 8 }}>Changes together with</h3>
            {pairs.slice(0, 6).map((pair) => {
              const other = pair.left_path === path ? pair.right_path : pair.left_path;
              return <div className="small" key={other} style={{ padding: "4px 0" }}><code>{other}</code> <span className="muted">in {pair.commit_count} commits</span></div>;
            })}
          </div>
        )}
      </div>
    </Panel>
  );
}
