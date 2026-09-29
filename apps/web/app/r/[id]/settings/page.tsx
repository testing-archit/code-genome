"use client";

import type { RepositoryConnection } from "@code-genome/contracts";
import { FormEvent, useEffect, useState } from "react";

import { LockIcon } from "../../../../components/icons";
import { useRepo } from "../../../../components/repo-context";
import { Loading, Notice, Panel } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, errorMessage } from "../../../../lib/api";
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
      <Panel title="How the token is handled">
        <ul className="small" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 8 }}>
          <li>Encrypted with AES-256-GCM, bound to this workspace and repository.</li>
          <li>Handed to Git only through a short-lived helper; never written into URLs or logs.</li>
          <li>Removing it erases the stored ciphertext and records an audit event.</li>
          <li>Repository <code>{repository.external_id}</code>, branch <code>{repository.default_branch}</code>, ID <code>{repository.id}</code>.</li>
        </ul>
      </Panel>
    </div>
  );
}
