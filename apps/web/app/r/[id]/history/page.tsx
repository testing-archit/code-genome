"use client";

import type { EvolutionTimeline, MlOverviewWithInstability, TimelinePoint } from "@code-genome/contracts";
import { useMemo, useState } from "react";

import { RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel, SearchField } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { formatDate, formatTime, localDayKey, parseTime, relativeTime, shortSha } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

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
    // Bucket keys, the 60-day axis, and the header label all use the viewer's local calendar day.
    const newest = parseTime(commits[0].authored_at);
    const buckets = new Map<string, number>();
    commits.forEach((commit) => {
      const key = localDayKey(parseTime(commit.authored_at));
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    });
    return Array.from({ length: 60 }, (_, index) => {
      const key = localDayKey(new Date(newest.getFullYear(), newest.getMonth(), newest.getDate() - (59 - index)));
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

      <ComponentLanes />

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

const LANES = 10;

function ComponentLanes() {
  const { repository, published, models } = useRepo();
  const [bucket, setBucket] = useState<"week" | "month">("week");
  const [showAll, setShowAll] = useState(false);
  const sha = published?.snapshot_sha ?? "";
  const timeline = useResource(`${repository.id}:timeline:${sha}:${bucket}`, () => api.getTimeline(repository.id, bucket));
  const forecast = useMemo(() => {
    const instability = (models.data as MlOverviewWithInstability | null)?.tasks.instability?.result;
    return new Map((instability?.status === "trained" ? instability.predictions : []).map((item) => [item.component, item]));
  }, [models.data]);

  const view = useMemo(() => (timeline.data ? lanes(timeline.data, bucket === "week" ? 26 : 18) : null), [timeline.data, bucket]);

  const toggle = (
    <div aria-label="Bucket size" className="segmented" role="group">
      <button aria-pressed={bucket === "week"} onClick={() => setBucket("week")} type="button">Weekly</button>
      <button aria-pressed={bucket === "month"} onClick={() => setBucket("month")} type="button">Monthly</button>
    </div>
  );

  return (
    <Panel
      title="Component evolution"
      description="Churn per component over time, with bug-fix commits and candidate bug-introducing commits (SZZ-lite) marked. Shows when a module turned into a hotspot."
      actions={toggle}
    >
      {timeline.error ? (
        <Notice tone="error" title="The component timeline could not be loaded">{timeline.error}</Notice>
      ) : !view ? (
        <Loading rows={5} height={28} />
      ) : view.components.length === 0 ? (
        <Empty title="No component history">No supporting evidence was identified in the selected scope.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <div className="legend">
            <span><i style={{ "--swatch": "var(--hema)" } as React.CSSProperties} />churn (lines changed)</span>
            <span><i style={{ "--swatch": "var(--eosin)" } as React.CSSProperties} />bug-fix commits</span>
            <span><i style={{ "--swatch": "var(--warn)" } as React.CSSProperties} />candidate bug-introducing commits (heuristic)</span>
          </div>
          <div className="lanes" role="table" aria-label={`Churn per component per ${bucket}`}>
            <Lane label="Whole repository" points={view.overall} peak={view.peakOverall} buckets={view.buckets} />
            {(showAll ? view.components : view.components.slice(0, LANES)).map((component) => {
              const prediction = forecast.get(component.name);
              return (
                <Lane
                  buckets={view.buckets}
                  key={component.name}
                  label={component.name}
                  peak={view.peak}
                  points={component.points}
                  role={component.role}
                  badge={prediction ? (
                    <span
                      className={`badge ${prediction.band === "high" ? "badge-bad" : prediction.band === "medium" ? "badge-warn" : ""}`}
                      title="Inferred forecast from the instability model: probability that the next period contains a bug fix"
                    >
                      {Math.round(prediction.probability * 100)}% next-period fix risk
                    </span>
                  ) : null}
                />
              );
            })}
          </div>
          <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
            {view.components.length > LANES && (
              <button className="button button-secondary" onClick={() => setShowAll((value) => !value)} type="button">
                {showAll ? "Show top components" : `Show all ${view.components.length} components`}
              </button>
            )}
            <span className="muted small">
              {view.buckets.length} {bucket}s from {view.buckets[0]} (UTC). Forecast badges come from the instability model on the Models page{forecast.size ? "" : ", which is not trained for this snapshot"}.
            </span>
          </div>
          {timeline.data?.limitations.map((item) => <p className="muted small" key={item}>{item}</p>)}
        </div>
      )}
    </Panel>
  );
}

function lanes(data: EvolutionTimeline, keep: number) {
  const buckets = data.buckets.slice(-keep);
  const pick = (points: TimelinePoint[]) => {
    const byBucket = new Map(points.map((point) => [point.bucket_start, point]));
    return buckets.map((start) => byBucket.get(start) ?? { bucket_start: start, commits: 0, churn: 0, fix_commits: 0, authors: 0, bug_introducing_commits: 0 });
  };
  const components = data.components
    .map((component) => ({ ...component, points: pick(component.points) }))
    .filter((component) => component.points.some((point) => point.commits > 0))
    .sort((a, b) => b.points.reduce((sum, point) => sum + point.churn, 0) - a.points.reduce((sum, point) => sum + point.churn, 0));
  const overall = pick(data.overall);
  return {
    buckets,
    overall,
    components,
    peak: Math.max(1, ...components.flatMap((component) => component.points.map((point) => point.churn))),
    peakOverall: Math.max(1, ...overall.map((point) => point.churn)),
  };
}

function Lane({ label, role, points, peak, buckets, badge }: { label: string; role?: string; points: TimelinePoint[]; peak: number; buckets: string[]; badge?: React.ReactNode }) {
  return (
    <div className="lane" role="row">
      <div className="lane-label" role="rowheader">
        <code className="truncate" title={label}>{label}</code>
        <div className="chip-row">
          {role && role !== "unknown" && <span className="badge">{role}</span>}
          {badge}
        </div>
      </div>
      <div className="lane-cells" style={{ gridTemplateColumns: `repeat(${buckets.length}, minmax(0, 1fr))` }}>
        {points.map((point) => (
          <div
            className="lane-cell"
            key={point.bucket_start}
            role="cell"
            title={`${point.bucket_start}: ${point.commits} commits, ${point.churn} lines churned, ${point.fix_commits} fixes, ${point.bug_introducing_commits} candidate bug-introducing, ${point.authors} authors`}
          >
            <i style={{ height: `${point.churn ? Math.max(6, Math.sqrt(point.churn / peak) * 100) : 0}%` }} />
            {point.fix_commits > 0 && <b className="lane-fix" />}
            {point.bug_introducing_commits > 0 && <b className="lane-intro" />}
          </div>
        ))}
      </div>
    </div>
  );
}
