"use client";

import Link from "next/link";

import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Meter, Notice, Panel } from "../../../../components/ui";
import { moduleColor, nodeTitle } from "../../../../lib/format";

export default function ArchitecturePage() {
  return (
    <RequiresSnapshot what="Architecture">
      <ArchitectureView />
    </RequiresSnapshot>
  );
}

function ArchitectureView() {
  const { repository, architecture } = useRepo();
  if (architecture.error) return <Notice tone="error" title="Architecture could not be loaded">{architecture.error}</Notice>;
  if (!architecture.data) return <Panel><Loading rows={6} /></Panel>;
  const data = architecture.data;

  return (
    <>
      <Notice>Modules are inferred from directory structure and co-change history. Treat them as a map to check, not a statement of design intent.</Notice>
      <div className="split">
        <Panel title="Modules" description={`${data.modules.length} inferred by ${data.analysis_version}`} flush>
          {data.modules.length === 0 ? <Empty title="No modules inferred">The snapshot has too few files or too little history.</Empty> : (
            <div>
              {data.modules.map((module, index) => (
                <article className="claim" key={module.id}>
                  <div className="claim-top">
                    <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
                      <span style={{ width: 12, height: 12, borderRadius: 3, background: moduleColor(index), flex: "none" }} />
                      <h3 className="truncate">{module.name}</h3>
                    </div>
                    <span className="badge">{module.file_paths.length} files</span>
                  </div>
                  <p className="small">{module.description}</p>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <Meter value={module.confidence} />
                    <span className="muted small">{Math.round(module.confidence * 100)}% confidence{module.inferred ? ", inferred" : ""}</span>
                  </div>
                  <EvidenceChips ids={module.citations} limit={4} />
                </article>
              ))}
            </div>
          )}
        </Panel>

        <div style={{ display: "grid", gap: 20 }}>
          <Panel title="Changes together" description="File pairs that repeatedly change in the same commits." flush>
            {data.co_changes.length === 0 ? <Empty title="No repeated pairs">No two files changed together often enough.</Empty> : (
              <div className="list">
                {data.co_changes.slice(0, 12).map((edge) => (
                  <div className="list-row" key={`${edge.left_path}:${edge.right_path}`}>
                    <div className="grow">
                      <div className="truncate small"><Link href={`/r/${repository.id}/files?path=${encodeURIComponent(edge.left_path)}`}>{nodeTitle(edge.left_path)}</Link> and <Link href={`/r/${repository.id}/files?path=${encodeURIComponent(edge.right_path)}`}>{nodeTitle(edge.right_path)}</Link></div>
                      <small>{edge.commit_count} shared commits</small>
                    </div>
                    <Meter value={edge.confidence} />
                    <span className="score">{Math.round(edge.confidence * 100)}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Hotspots" description="Commit frequency plus churn, relative within this repository." flush>
            {data.hotspots.length === 0 ? <Empty title="No hotspots">Not enough history.</Empty> : (
              <div className="list">
                {data.hotspots.slice(0, 10).map((hotspot) => (
                  <Link className="list-row" href={`/r/${repository.id}/files?path=${encodeURIComponent(hotspot.path)}`} key={hotspot.path}>
                    <code className="grow truncate">{hotspot.path}</code>
                    <Meter tone="eosin" value={hotspot.score} />
                    <span className="score">{Math.round(hotspot.score * 100)}</span>
                  </Link>
                ))}
              </div>
            )}
          </Panel>
          <div className="limitations">{data.limitations.map((item) => <span key={item}>{item}</span>)}</div>
        </div>
      </div>
    </>
  );
}
