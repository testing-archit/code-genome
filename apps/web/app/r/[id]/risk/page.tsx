"use client";

import { useState } from "react";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel, SearchField } from "../../../../components/ui";
import { api } from "../../../../lib/api";
import { useResource } from "../../../../lib/use-resource";

export default function RiskPage() {
  return (
    <RequiresSnapshot what="Risk and impact">
      <RiskView />
    </RequiresSnapshot>
  );
}

function RiskView() {
  const { repository, published, models } = useRepo();
  const sha = published?.snapshot_sha ?? "";
  const trainedAt = models.data?.tasks.defect_risk?.trained_at ?? "none";
  const risk = useResource(`${repository.id}:risk:${sha}:${trainedAt}`, () => api.getRisk(repository.id));
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const selected = picked ?? risk.data?.scores[0]?.path ?? null;
  const impact = useResource(selected ? `${repository.id}:impact:${sha}:${selected}` : null, () => api.getImpact(repository.id, selected ?? ""));
  const scores = (risk.data?.scores ?? []).filter((item) => item.path.toLowerCase().includes(query.trim().toLowerCase()));
  const detail = risk.data?.scores.find((item) => item.path === selected) ?? null;
  const learned = risk.data?.scores[0]?.model_version.startsWith("defect") ?? false;

  if (risk.error) return <Notice tone="error" title="Risk could not be ranked">{risk.error}</Notice>;

  return (
    <div className="split">
      <Panel
        title={learned ? "Predicted defect-proneness" : "Relative change risk"}
        description={
          learned
            ? `Probability that a bug-fix commit touches the file next, from ${risk.data?.scores[0]?.model_version}. See Models for its evaluation.`
            : "Ranks files against each other with a transparent heuristic. Train models on the Models tab for learned probabilities."
        }
        actions={<div style={{ width: 220 }}><SearchField label="Filter ranked files" onChange={setQuery} placeholder="Filter" value={query} /></div>}
        flush
      >
        {risk.loading ? <Loading rows={8} /> : scores.length === 0 ? <Empty title="Nothing ranked">Risk needs change history. Analyze a branch with more commits.</Empty> : (
          <div className="list" style={{ maxHeight: "70vh", overflow: "auto" }}>
            {scores.slice(0, 100).map((item, index) => (
              <button aria-pressed={item.path === selected} className="list-row" key={item.path} onClick={() => setPicked(item.path)} type="button">
                <span className="muted small" style={{ width: 24, flex: "none", fontVariantNumeric: "tabular-nums" }}>{index + 1}</span>
                <div className="grow"><code className="truncate" style={{ display: "block" }}>{item.path}</code><small className="truncate">{item.rationale}</small></div>
                <Meter tone="eosin" value={item.score} />
                <span className="score">{Math.round(item.score * 100)}{learned ? "%" : ""}</span>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <div style={{ display: "grid", gap: 20 }}>
        {detail && (
          <Panel title={learned ? "Why the model predicts this" : "Why it ranks here"} description={<code style={{ overflowWrap: "anywhere" }}>{detail.path}</code>}>
            <div style={{ display: "grid", gap: 12 }}>
              <p className="small">{detail.rationale}</p>
              <dl className="kv">
                {Object.entries(detail.features).map(([name, value]) => (
                  <div key={name} style={{ display: "contents" }}><dt>{name.replaceAll("_", " ")}</dt><dd>{Number(value).toFixed(2)}</dd></div>
                ))}
                <dt>Model</dt><dd>{detail.model_version}</dd>
              </dl>
              <EvidenceChips ids={detail.evidence_ids} limit={6} />
            </div>
          </Panel>
        )}
        <Panel title="Impact radius" description="One hop through observed imports and repeated co-change." flush>
          {!selected ? <Empty title="Pick a file">Choose a ranked file to see what it touches.</Empty> : impact.loading ? <Loading rows={4} /> : impact.data?.impacted.length ? (
            <div className="list">
              {impact.data.impacted.slice(0, 12).map((item) => (
                <div className="list-row" key={item.path} style={{ alignItems: "flex-start" }}>
                  <div className="grow" style={{ display: "grid", gap: 6 }}>
                    <code className="truncate">{item.path}</code>
                    <small>{item.reasons.join(", ")}</small>
                    <EvidenceChips ids={item.evidence_ids} limit={3} />
                  </div>
                  <span className="score">{Math.round(item.score * 100)}</span>
                </div>
              ))}
            </div>
          ) : <Empty title="No observed neighbours">Nothing imports this file or changes with it repeatedly. That does not rule out runtime impact.</Empty>}
        </Panel>
        {risk.data && <div className="limitations">{risk.data.limitations.map((item) => <span key={item}>{item}</span>)}</div>}
      </div>
    </div>
  );
}
