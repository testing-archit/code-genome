"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DownloadIcon } from "../../../../components/icons";
import { Markdown } from "../../../../components/markdown";
import { RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Loading, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, errorMessage } from "../../../../lib/api";
import { formatTime, shortSha } from "../../../../lib/format";
import { useResource } from "../../../../lib/use-resource";

export default function DocsPage() {
  return (
    <RequiresSnapshot what="Generated docs">
      <Suspense fallback={<Panel><Loading rows={8} /></Panel>}>
        <Docs />
      </Suspense>
    </RequiresSnapshot>
  );
}

function Docs() {
  const { repository, published, openEvidence } = useRepo();
  const { toast } = useWorkspace();
  const params = useSearchParams();
  const docs = useResource(`${repository.id}:docs:${published?.snapshot_sha ?? ""}`, () => api.getDocs(repository.id));
  const [downloading, setDownloading] = useState(false);
  const requested = params.get("doc");
  const current = docs.data?.documents.find((item) => item.name === requested) ?? docs.data?.documents[0] ?? null;

  async function download(name: string) {
    setDownloading(true);
    try {
      await api.downloadDoc(repository.id, name);
    } catch (caught) {
      toast(errorMessage(caught, "The document could not be downloaded."));
    } finally {
      setDownloading(false);
    }
  }

  if (docs.error) return <Notice tone="error" title="Documentation could not be generated">{docs.error}</Notice>;
  if (!docs.data || !current) return <Panel><Loading rows={8} /></Panel>;

  return (
    <div className="docs-layout">
      <nav aria-label="Generated documents" className="docs-index">
        {docs.data.documents.map((item) => (
          <Link
            aria-current={item.name === current.name ? "page" : undefined}
            className="docs-index-link"
            href={`/r/${repository.id}/docs?doc=${encodeURIComponent(item.name)}`}
            key={item.name}
            scroll={false}
          >
            <code>{item.name}</code>
            <small>{item.description}</small>
          </Link>
        ))}
        <p className="muted small" style={{ padding: "8px 10px" }}>
          Generated {formatTime(docs.data.generated_at)} from snapshot <code>{shortSha(docs.data.snapshot_sha, 10)}</code> by {docs.data.version}. Select a citation to inspect its source.
        </p>
      </nav>
      <Panel
        title={<code>{current.name}</code>}
        description={current.description}
        actions={
          <button className="button button-secondary button-small" disabled={downloading} onClick={() => void download(current.name)} type="button">
            <DownloadIcon size={15} />{downloading ? "Downloading…" : "Download"}
          </button>
        }
      >
        <Markdown onEvidence={openEvidence} source={current.markdown} />
      </Panel>
    </div>
  );
}
