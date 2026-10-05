"use client";

import type { ExportFormat, ExportKind } from "@code-genome/contracts";
import { useState } from "react";

import { api, errorMessage } from "../lib/api";
import { DownloadIcon } from "./icons";
import { useWorkspace } from "./workspace";

/** Download a cited Markdown or JSON report. Each download is recorded in the activity log. */
export function ExportButtons({
  repositoryId,
  kind,
  range,
  disabled = false,
}: {
  repositoryId: string;
  kind: ExportKind;
  range?: { base: string; head: string };
  disabled?: boolean;
}) {
  const { toast } = useWorkspace();
  const [busy, setBusy] = useState<ExportFormat | null>(null);

  async function download(format: ExportFormat) {
    setBusy(format);
    try {
      await api.downloadExport(repositoryId, kind, format, range);
    } catch (caught) {
      toast(errorMessage(caught, "The export could not be downloaded."));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div aria-label={`Export ${kind}`} role="group" style={{ display: "flex", gap: 6, flex: "none" }}>
      {(["md", "json"] as const).map((format) => (
        <button
          className="button button-secondary button-small"
          disabled={disabled || busy !== null}
          key={format}
          onClick={() => void download(format)}
          title={format === "md" ? "Download a cited Markdown report" : "Download the full data as JSON"}
          type="button"
        >
          <DownloadIcon size={15} />
          {busy === format ? "Exporting…" : format === "md" ? "Markdown" : "JSON"}
        </button>
      ))}
    </div>
  );
}
