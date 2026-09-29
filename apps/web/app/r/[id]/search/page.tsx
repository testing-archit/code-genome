"use client";

import type { SearchResults } from "@code-genome/contracts";
import Link from "next/link";
import { FormEvent, useState } from "react";

import { SearchIcon } from "../../../../components/icons";
import { RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Notice, Panel } from "../../../../components/ui";
import { api, errorMessage } from "../../../../lib/api";

const kinds = [
  { value: "", label: "Everything" },
  { value: "file", label: "Files" },
  { value: "commit", label: "Commits" },
  { value: "module", label: "Modules" },
] as const;

const examples = ["invoice export", "sessionToken refresh", "login kahan handle hota hai", "टेस्ट फ़ाइलें"];

export default function SearchPage() {
  return (
    <RequiresSnapshot what="Search">
      <SearchView />
    </RequiresSnapshot>
  );
}

function SearchView() {
  const { repository, openEvidence } = useRepo();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(text: string, filter = kind) {
    if (text.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResults(await api.search(repository.id, text.trim(), filter || undefined));
    } catch (caught) {
      setError(errorMessage(caught, "Search failed."));
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(query);
  }

  return (
    <>
      <Panel title="Search the repository" description="Keyword ranking (BM25) and LSA semantic embeddings, combined in whichever mode scored best on this repository (see Models). Identifiers are split, so “invoice export” finds exportInvoice. Hinglish and common Hindi terms work too.">
        <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
          <div className="composer-box">
            <SearchIcon />
            <label className="sr-only" htmlFor="repo-search">Search</label>
            <input autoFocus id="repo-search" onChange={(event) => setQuery(event.target.value)} placeholder="What are you looking for?" style={{ flex: 1, border: 0, outline: "none", background: "none", minHeight: 36 }} value={query} />
            <button className="button button-primary" disabled={loading || query.trim().length < 2} type="submit">{loading ? "Searching…" : "Search"}</button>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
            <div className="segmented" role="group" aria-label="Result type">
              {kinds.map((item) => (
                <button aria-pressed={kind === item.value} key={item.value} onClick={() => { setKind(item.value); if (results) void run(results.query, item.value); }} type="button">{item.label}</button>
              ))}
            </div>
            <div className="suggestions">
              {examples.map((item) => <button className="suggestion" key={item} onClick={() => { setQuery(item); void run(item); }} type="button">{item}</button>)}
            </div>
          </div>
        </form>
      </Panel>

      {error && <Notice tone="error">{error}</Notice>}
      {results && (
        <Panel title={`${results.hits.length} results`} description={`“${results.query}” in snapshot ${results.snapshot_sha.slice(0, 10)} · ${results.model_version}`} flush>
          {results.hits.length === 0 ? <Empty title="No matches">{results.message}</Empty> : (
            <div>
              {results.hits.map((hit) => (
                <div className="search-hit" key={hit.id}>
                  <div className="search-hit-top">
                    <span className="badge">{hit.kind}</span>
                    {hit.kind === "file" && hit.path ? (
                      <Link className="truncate" href={`/r/${repository.id}/files?path=${encodeURIComponent(hit.path)}`}><code>{hit.title}</code></Link>
                    ) : (
                      <button className="truncate" onClick={() => openEvidence(hit.id)} style={{ border: 0, background: "none", padding: 0, textAlign: "left", color: "var(--hema)" }} type="button">{hit.title}</button>
                    )}
                    <span className={`badge ${hit.match === "keyword and semantic" ? "badge-hema" : hit.match === "keyword" ? "" : "badge-warn"}`} style={{ marginLeft: "auto" }}>{hit.match}</span>
                  </div>
                  <div className="muted small">
                    Keyword score {hit.bm25.toFixed(2)}{hit.bm25_rank ? ` (rank ${hit.bm25_rank})` : ""} · semantic similarity {hit.semantic.toFixed(2)}{hit.semantic_rank ? ` (rank ${hit.semantic_rank})` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="panel-body" style={{ borderTop: "1px solid var(--rule)" }}>
            <div className="limitations">{results.limitations.map((item) => <span key={item}>{item}</span>)}</div>
          </div>
        </Panel>
      )}
    </>
  );
}
