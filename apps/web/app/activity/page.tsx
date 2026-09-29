"use client";

import type { AuditEventRecord } from "@code-genome/contracts";
import { useEffect, useMemo, useState } from "react";

import { DownloadIcon } from "../../components/icons";
import { TopBar } from "../../components/shell";
import { Empty, Loading, Notice, Panel, SearchField } from "../../components/ui";
import { useWorkspace } from "../../components/workspace";
import { api, errorMessage } from "../../lib/api";
import { formatTime, relativeTime } from "../../lib/format";

export default function ActivityPage() {
  const { toast } = useWorkspace();
  const [events, setEvents] = useState<AuditEventRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let active = true;
    api
      .listAuditEvents()
      .then((items) => {
        if (active) setEvents(items);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught, "The activity log could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (events ?? []).filter((event) => !needle || `${event.action} ${event.actor_id} ${event.resource_type} ${event.resource_id}`.toLowerCase().includes(needle));
  }, [events, query]);

  return (
    <>
      <TopBar
        title="Activity log"
        detail="Workspace audit events"
        actions={<button className="button button-secondary" onClick={() => void api.downloadAuditCsv().catch((caught: unknown) => toast(errorMessage(caught, "Export failed.")))} type="button"><DownloadIcon size={16} />Export CSV</button>}
      />
      <div className="content">
        {error ? <Notice tone="error" title="Activity is unavailable">{error} Only workspace owners and admins can read the audit log.</Notice> : (
          <Panel
            title="Recent events"
            description="Every answer, analysis, token change, and voice session is recorded. Opening this page is recorded too."
            actions={<div style={{ width: 260 }}><SearchField label="Filter events" onChange={setQuery} placeholder="Filter by action or actor" value={query} /></div>}
            flush
          >
            {events === null ? <Loading rows={8} /> : filtered.length === 0 ? <Empty title="No events match">Clear the filter to see everything.</Empty> : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Resource</th><th>Request</th></tr></thead>
                  <tbody>
                    {filtered.map((event) => (
                      <tr key={event.id}>
                        <td title={formatTime(event.created_at)}>{relativeTime(event.created_at)}</td>
                        <td><span className="badge badge-hema">{event.action}</span></td>
                        <td><code>{event.actor_id}</code></td>
                        <td><span className="muted">{event.resource_type}</span> <code>{event.resource_id.slice(0, 16)}</code></td>
                        <td><code className="muted">{event.request_id.slice(0, 8)}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        )}
      </div>
    </>
  );
}
