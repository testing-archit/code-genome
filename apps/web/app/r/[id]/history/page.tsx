"use client";

import { useMemo, useState } from "react";

import { RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel, SearchField } from "../../../../components/ui";
import { formatDate, formatTime, parseTime, relativeTime, shortSha } from "../../../../lib/format";

export default function HistoryPage() {
  return (
    <RequiresSnapshot what="Commit history">
      <HistoryView />
    </RequiresSnapshot>
  );
}

function HistoryView() {
  const { repository, inventory, models } = useRepo();
  const [intentFilter, setIntentFilter] = useState<string | null>(null);
  const intents = useMemo(
    () => new Map(models.data?.tasks.commit_intent?.result.predictions.map((item) => [item.sha, item]) ?? []),
    [models.data],
  );
  const anomalies = useMemo(
    () => new Map(models.data?.tasks.anomalies?.result.anomalies.map((item) => [item.sha, item]) ?? []),
    [models.data],
  );
  const [query, setQuery] = useState("");
  const [author, setAuthor] = useState<string | null>(null);
  const commits = useMemo(() => inventory.data?.commits ?? [], [inventory.data]);

  const authors = useMemo(() => {
    const counts = new Map<string, number>();
    commits.forEach((commit) => counts.set(commit.author_name, (counts.get(commit.author_name) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [commits]);

  const days = useMemo(() => {
    if (commits.length === 0) return [];
    const newest = parseTime(commits[0].authored_at);
    const buckets = new Map<string, number>();
    commits.forEach((commit) => {
      const key = commit.authored_at.slice(0, 10);
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    });
    return Array.from({ length: 60 }, (_, index) => {
      const day = new Date(newest);
      day.setUTCDate(day.getUTCDate() - (59 - index));
      const key = day.toISOString().slice(0, 10);
      return { key, count: buckets.get(key) ?? 0 };
    });
  }, [commits]);
  const peak = Math.max(1, ...days.map((day) => day.count));

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return commits.filter(
      (commit) =>
        (!author || commit.author_name === author) &&
        (!intentFilter || (intentFilter === "unusual" ? anomalies.has(commit.sha) : intents.get(commit.sha)?.intent === intentFilter)) &&
        (!needle || commit.message.toLowerCase().includes(needle) || commit.sha.startsWith(needle)),
    );
  }, [commits, author, query, intentFilter, intents, anomalies]);
  const intentCounts = useMemo(() => {
    const counts = new Map<string, number>();
    intents.forEach((item) => counts.set(item.intent, (counts.get(item.intent) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [intents]);

  if (inventory.error) return <Notice tone="error" title="History could not be loaded">{inventory.error}</Notice>;
  if (inventory.loading) return <Panel><Loading rows={8} /></Panel>;
  if (commits.length === 0) return <Panel><Empty title="No commits observed">The mirror returned no history for {repository.default_branch}.</Empty></Panel>;

  const base = repository.clone_url.replace(/\.git$/, "");
  return (
    <>
      <div className="grid-2">
        <Panel title="Commit activity" description={`The 60 days up to the newest observed commit (${formatDate(commits[0].authored_at)}).`}>
          <div className="activity-chart" role="img" aria-label="Commits per day">
            {days.map((day) => (
              <i data-empty={day.count === 0} key={day.key} style={{ height: `${(day.count / peak) * 100}%` }} title={`${day.key}: ${day.count} commits`} />
            ))}
          </div>
          <p className="muted small" style={{ marginTop: 10 }}>{commits.length} commits read from the mirror; older history may exist beyond the analysis window.</p>
        </Panel>
        <Panel title="Authors" description="Git author names as recorded. They are not verified identities." flush>
          <div className="list" style={{ maxHeight: 220, overflow: "auto" }}>
            {authors.slice(0, 12).map(([name, count]) => (
              <button aria-pressed={author === name} className="list-row" key={name} onClick={() => setAuthor(author === name ? null : name)} type="button">
                <span className="grow truncate">{name}</span>
                <span className="muted small">{count} commits</span>
              </button>
            ))}
          </div>
        </Panel>
      </div>

      {intentCounts.length > 0 && (
        <div className="chip-row" role="group" aria-label="Filter by commit intent">
          <button aria-pressed={intentFilter === null} className="suggestion" onClick={() => setIntentFilter(null)} type="button">All intents</button>
          {intentCounts.map(([intent, count]) => (
            <button aria-pressed={intentFilter === intent} className="suggestion" key={intent} onClick={() => setIntentFilter(intentFilter === intent ? null : intent)} style={intentFilter === intent ? { borderColor: "var(--hema)", color: "var(--hema)" } : undefined} type="button">{intent} ({count})</button>
          ))}
          {anomalies.size > 0 && <button className="suggestion" onClick={() => setIntentFilter(intentFilter === "unusual" ? null : "unusual")} style={intentFilter === "unusual" ? { borderColor: "var(--hema)", color: "var(--hema)" } : undefined} type="button">unusual ({anomalies.size})</button>}
          <span className="muted small">Intents come from the commit-intent classifier; see Models.</span>
        </div>
      )}

      <Panel
        title="Commits"
        description={author ? `Showing commits by ${author}` : `Newest first on ${repository.default_branch}`}
        actions={<div style={{ width: 260 }}><SearchField label="Search commits" onChange={setQuery} placeholder="Search messages or SHA" value={query} /></div>}
        flush
      >
        {filtered.length === 0 ? <Empty title="No commits match">Clear the author or search filter.</Empty> : (
          <div>
            {filtered.slice(0, 200).map((commit) => (
              <div className="commit" data-merge={commit.parent_shas.length > 1} key={commit.sha}>
                <span className="commit-dot" />
                <div style={{ minWidth: 0 }}>
                  <div className="commit-msg">{commit.message.split("\n")[0]}</div>
                  <div className="commit-meta">
                    <span>{commit.author_name}</span>
                    <span title={formatTime(commit.authored_at)}>{relativeTime(commit.authored_at)}</span>
                    {commit.parent_shas.length > 1 && <span className="badge">merge</span>}
                    {intents.get(commit.sha) && (
                      <span
                        className="intent-badge"
                        data-intent={intents.get(commit.sha)?.intent}
                        title={intents.get(commit.sha)?.source === "conventional" ? "Labelled by the author's commit prefix" : `Predicted by the intent classifier, ${Math.round((intents.get(commit.sha)?.probability ?? 0) * 100)}% confident`}
                      >
                        {intents.get(commit.sha)?.intent}{intents.get(commit.sha)?.source === "model" ? ` ${Math.round((intents.get(commit.sha)?.probability ?? 0) * 100)}%` : ""}
                      </span>
                    )}
                    {anomalies.get(commit.sha) && <span className="badge badge-warn" title={anomalies.get(commit.sha)?.reasons.join("; ")}>unusual: {anomalies.get(commit.sha)?.reasons[0]}</span>}
                  </div>
                </div>
                <a className="mono muted" href={`${base}/commit/${commit.sha}`} rel="noreferrer" target="_blank">{shortSha(commit.sha, 8)}</a>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
