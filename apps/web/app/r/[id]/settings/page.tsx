"use client";

import type { RepositoryAutomation, RepositoryConnection } from "@code-genome/contracts";
import { FormEvent, useEffect, useState } from "react";

import { LockIcon } from "../../../../components/icons";
import { useRepo } from "../../../../components/repo-context";
import { Loading, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, apiUrl, errorMessage } from "../../../../lib/api";
import { formatTime } from "../../../../lib/format";

export default function SettingsPage() {
  const { repository } = useRepo();
  const { toast } = useWorkspace();
  const [connection, setConnection] = useState<RepositoryConnection | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getConnection(repository.id)
      .then((value) => {
        if (active) setConnection(value);
      })
      .catch((caught: unknown) => {
        if (active) setLoadError(errorMessage(caught, "Private access settings could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, [repository.id]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      setConnection(await api.putConnection(repository.id, token.trim()));
      setToken("");
      toast("Token saved. Run the analysis again to use it.");
    } catch (caught) {
      setError(errorMessage(caught, "The token could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  async function revoke() {
    setSaving(true);
    setError(null);
    try {
      await api.deleteConnection(repository.id);
      setConnection(await api.getConnection(repository.id));
      toast("Token removed and erased");
    } catch (caught) {
      setError(errorMessage(caught, "The token could not be removed."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="split">
      <Panel title="Private repository access" description="Needed only for private repositories. Owners and admins can change it.">
        {loadError ? <Notice tone="error">{loadError}</Notice> : !connection ? <Loading rows={2} /> : (
          <div style={{ display: "grid", gap: 18 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <LockIcon />
              {connection.connected ? (
                <div><strong>Connected with a {connection.token_kind === "installation_token" ? "GitHub App token" : "fine-grained token"}</strong><p className="muted small">Added {formatTime(connection.installed_at)} · scopes {connection.scopes.join(", ")} · key {connection.key_version}</p></div>
              ) : (
                <div><strong>No token stored</strong><p className="muted small">{connection.revoked_at ? `Last token removed ${formatTime(connection.revoked_at)}.` : "Public repositories do not need one."}</p></div>
              )}
            </div>
            <form onSubmit={save} style={{ display: "grid", gap: 12 }}>
              <label className="field">
                {connection.connected ? "Replace token" : "Fine-grained personal access token"}
                <input autoComplete="off" className="input" minLength={8} onChange={(event) => setToken(event.target.value)} placeholder="github_pat_…" required type="password" value={token} />
                <small>Give it read-only access to Contents for this repository only.</small>
              </label>
              {error && <Notice tone="error">{error}</Notice>}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="button button-primary" disabled={saving || token.trim().length < 8} type="submit">{saving ? "Saving…" : "Save token"}</button>
                {connection.connected && <button className="button button-danger" disabled={saving} onClick={() => void revoke()} type="button">Remove token</button>}
              </div>
            </form>
          </div>
        )}
      </Panel>
      <div style={{ display: "grid", gap: 20, alignContent: "start" }}>
      <AutomationPanel repositoryId={repository.id} />
      <Panel title="How the token is handled">
        <ul className="small" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 8 }}>
          <li>Encrypted with AES-256-GCM, bound to this workspace and repository.</li>
          <li>Handed to Git only through a short-lived helper; never written into URLs or logs.</li>
          <li>Removing it erases the stored ciphertext and records an audit event.</li>
          <li>Repository <code>{repository.external_id}</code>, branch <code>{repository.default_branch}</code>, ID <code>{repository.id}</code>.</li>
        </ul>
      </Panel>
      </div>
    </div>
  );
}

function AutomationPanel({ repositoryId }: { repositoryId: string }) {
  const { toast } = useWorkspace();
  const [automation, setAutomation] = useState<RepositoryAutomation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .getAutomation(repositoryId)
      .then((value) => {
        if (active) setAutomation(value);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught, "Automation settings could not be loaded."));
      });
    return () => {
      active = false;
    };
  }, [repositoryId]);

  async function toggle(next: boolean) {
    setSaving(true);
    setError(null);
    try {
      setAutomation(await api.putAutomation(repositoryId, next));
      toast(next ? "Pushes will now queue an analysis" : "Automatic analysis turned off");
    } catch (caught) {
      setError(errorMessage(caught, "The setting could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  const endpoint = automation ? `${apiUrl.replace(/\/api\/v1\/?$/, "")}${automation.webhook_path}` : "";

  return (
    <Panel title="Automatic analysis" description="Re-analyze when GitHub reports a push to the tracked branch. Owners and admins can change it.">
      {!automation ? (error ? <Notice tone="error">{error}</Notice> : <Loading rows={2} />) : (
        <div style={{ display: "grid", gap: 14 }}>
          <label style={{ display: "flex", gap: 10, alignItems: "center", cursor: saving ? "wait" : "pointer" }}>
            <input checked={automation.auto_analyze} disabled={saving} onChange={(event) => void toggle(event.target.checked)} type="checkbox" />
            <span>Analyze each push to <code>{automation.branch}</code></span>
          </label>
          {error && <Notice tone="error">{error}</Notice>}
          {!automation.webhook_configured && (
            <Notice tone="warn" title="Webhook secret not set">
              The API rejects deliveries until <code>CODE_GENOME_GITHUB_WEBHOOK_SECRET</code> is configured on the server.
            </Notice>
          )}
          <dl className="kv">
            <dt>Payload URL</dt><dd><code style={{ overflowWrap: "anywhere" }}>{endpoint}</code></dd>
            <dt>Content type</dt><dd><code>application/json</code></dd>
            <dt>Events</dt><dd>{automation.events.join(", ")}</dd>
            <dt>Secret</dt><dd>Same value as <code>CODE_GENOME_GITHUB_WEBHOOK_SECRET</code></dd>
          </dl>
          <p className="muted small">Signatures are verified and each delivery ID is accepted once. Pushes that arrive while an analysis is running are folded into it. GitHub must be able to reach this URL, so local development needs a tunnel.</p>
        </div>
      )}
    </Panel>
  );
}
