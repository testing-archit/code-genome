# API Specification

Base path: `/api/v1`. JSON requests/responses. OIDC session/JWT required except health. All resources are workspace-scoped by authorization, never by a client-provided workspace ID alone.

## Core endpoints

| Method/path | Purpose | Success |
|---|---|---|
| `POST /repositories` | register approved repo | `201 Repository` |
| `PUT /repositories/{id}/connection` | encrypt/rotate private GitHub access | `200 RepositoryConnection` |
| `GET /repositories/{id}/connection` | connection metadata, never the secret | `200 RepositoryConnection` |
| `DELETE /repositories/{id}/connection` | revoke and erase credential envelope | `204` |
| `POST /repositories/{id}/analyses` | queue snapshot analysis | `202 AnalysisRun` |
| `GET /analyses/{id}` | job progress/diagnostics, live `stage` (`fetching` → `indexing` → `mining_history` → `evolution` → `tracing_bugs` → `parsing` → `publishing` → `complete`) and `progress_counts` (files, commits, co-change pairs, modules, fix commits, bug links, dependencies, calls, knowledge excerpts) | `200 AnalysisRun` |
| `GET /repositories/{id}/inventory` | bounded refs, commits, and file manifest | `200 RepositoryInventory` |
| `GET /repositories/{id}/architecture` | inferred modules, hotspots, co-change | `200 Architecture` |
| `GET /repositories/{id}/graph` | filtered snapshot graph | `200 GraphProjection` |
| `GET /evidence/{id}` | immutable source provenance locator | `200 Evidence` |
| `GET /repositories/{id}/impact?path=` | one-hop impact for a file; each item has the weighted `signals` (dependency, co-change, proximity, bug correlation), `weighted_score`, and `graph_metrics` (PageRank, its percentile, betweenness; context, not part of the score). Package nodes are excluded | `200 ImpactResult` |
| `POST /delivery-reports` | create report + claims | `201 DeliveryReport` |
| `POST /delivery-reports/{id}/assessments` | run deterministic verification | `200 DeliveryReport` |
| `GET /delivery-reports/{id}` | report, claims, assessments | `200 DeliveryReport` |
| `GET /delivery-reports/{id}/download` | download cited Markdown audit | `200 text/markdown` |
| `GET /repositories/{id}/risk` | explainable relative risk ranking | `200 RiskResult` |
| `GET /delivery-reports?repository_id=` | list checked reports for a repository | `200 DeliveryReport[]` |
| `POST /chat/answers` | grounded Q&A (`language`: `auto`/`en`/`hi`/`hinglish`, `channel`: `text`/`voice`) | `200 GroundedAnswer` |
| `GET /repositories/{id}/conversations` | caller's conversations for a repository | `200 ConversationSummary[]` |
| `POST /repositories/{id}/conversations` | start a conversation | `201 ConversationSummary` |
| `GET /conversations/{id}` | messages, each assistant turn with its cited answer | `200 Conversation` |
| `POST /conversations/{id}/messages` | ask a follow-up; history resolves references only | `201 ConversationTurn` |
| `POST /conversations/{id}/messages/stream` | same as above as `text/event-stream`: `status`, `delta` (unverified draft), `fallback`, `done` (stored `ConversationTurn`), `error`; nothing is stored unless the stream completes | `200 text/event-stream` |
| `POST /repositories/{id}/impact/change` | rank impact of a unified diff and/or path list against the latest snapshot | `200 ChangeImpact` |
| `GET /repositories/{id}/snapshots` | published snapshots, newest first | `200 SnapshotSummary[]` |
| `GET /repositories/{id}/compare?base=&head=` | files, imports, inferred modules, hotspots between two snapshots; sections missing from either side are listed in `unavailable` | `200 SnapshotComparison` |
| `GET /repositories/{id}/exports/{architecture\|risk\|comparison}?format=md\|json` | cited, audited report download | `200 text/markdown` or JSON |
| `GET/PUT /repositories/{id}/automation` | opt in to push-triggered re-analysis (owner/admin) | `200 RepositoryAutomation` |
| `POST /webhooks/github` | GitHub `push`/`ping`; HMAC `X-Hub-Signature-256` with `CODE_GENOME_GITHUB_WEBHOOK_SECRET`, delivery-ID replay protection | `202`/`200 WebhookResult` |
| `DELETE /conversations/{id}` | delete a conversation (answers stay audited) | `204` |
| `POST /voice/sessions` | mint a single-use Gemini Live token with locked setup | `201 VoiceSession` |
| `GET /repositories/{id}/ml` | trained models, evaluations, and outputs for the latest snapshot | `200 MlOverview` |
| `POST /repositories/{id}/ml/train` | retrain all snapshot models (replaces prior runs atomically) | `200 MlOverview` |
| `GET /repositories/{id}/search?q=&kind=` | ranked search with the repository's selected retrieval mode | `200 SearchResults` |
| `POST /chat/answers/{id}/feedback` | record answer feedback | `200` |
| `GET /repositories/{id}/overview` | health score, counts, riskiest files and components, instability forecast, README summary | `200 RepositoryOverview` |
| `GET /repositories/{id}/module-graph` | components with inferred role and reason, data stores, integrations, contributors, commit/fix counts, last change; import and co-change links | `200 ModuleGraph` |
| `GET /repositories/{id}/docs` | six generated Markdown documents with citations; `rewritten_markdown`/`rewrite` when an accepted model-written version exists for the same source text | `200 GeneratedDocuments` |
| `POST /repositories/{id}/docs/rewrite` | ask Gemini for readable versions; each is stored with model and source hash and accepted only if it keeps every citation and heading and cites nothing new (`docs-rewrite@1`). `409 GEMINI_NOT_CONFIGURED` without a key | `200 GeneratedDocuments` |
| `GET /repositories/{id}/docs/{name}/download` | download one generated document (audited) | `200 text/markdown` |
| `GET /repositories/{id}/genome?focus=&limit=` | Software Genome Graph: files, components, symbols, developers, commits, packages, data stores, external APIs; 16 edge types, each with evidence and `inferred`. `focus` (file path or component) returns its 2-hop neighbourhood | `200 GenomeGraph` |
| `GET /repositories/{id}/bugs?path=&limit=` | SZZ-lite history: fix commits and candidate bug-introducing commits with blamed lines, confidence, bulk/boundary flags; per-file counts. Candidates, not proof | `200 BugHistory` |
| `GET /repositories/{id}/timeline?bucket=week\|month&path=` | commits, churn, fixes, authors, and bug-introducing commits per component and bucket (UTC) | `200 EvolutionTimeline` |
| `POST /workspaces/{id}/retention/run` | preview/execute report retention | `200` |
| `GET /workspaces/{id}/audit-events` | audited JSON/CSV export | `200` |

## Create delivery report

```json
{
  "repository_id": "repo_123",
  "text": "Updated Fixed Deposit cards and navigation.",
  "scope": {
    "from": "2026-09-08T00:00:00Z",
    "to": "2026-09-11T23:59:59Z",
    "branches": ["prod", "main"],
    "include_prs": true,
    "include_ci": true,
    "include_deployments": true
  }
}
```

`201` returns report ID, parsed atomic claims, parser version, and `needs_review` flags. Text is never sent to third-party models unless the workspace policy permits it.

## Assessment response shape

```json
{
  "claim_id": "clm_01",
  "status": "PARTIALLY_VERIFIED",
  "confidence": 0.84,
  "summary": "Card components changed; no navigation evidence was identified.",
  "scope": {"snapshot_sha":"abc123", "branches":["prod"], "sources":["commit","pull_request"]},
  "evidence": [{"id":"ev_12","kind":"file_change","url":"...","relevance":0.93}],
  "limitations": ["Deployment provider was not connected."],
  "analysis_version": "delivery-auditor@0.1.0"
}
```

## Errors

Use RFC 9457 problem JSON: `type`, `title`, `status`, `detail`, `instance`, `request_id`.

- `400 INVALID_SCOPE`: invalid date/ref/query.
- `401 UNAUTHENTICATED`, `403 FORBIDDEN`, `404 NOT_FOUND` (avoid tenant existence leakage).
- `409 ANALYSIS_IN_PROGRESS` or duplicate idempotency key conflict.
- `422 UNPROCESSABLE_REPORT`: empty/oversized/unparseable report; preserve user text only per retention policy.
- `424 EVIDENCE_SOURCE_UNAVAILABLE`: assessment may return partial results with limitation.
- `413 REQUEST_TOO_LARGE`; `429 RATE_LIMITED`; `503 ANALYSIS_DEGRADED`.

Production bearer tokens are validated against configured JWKS with fixed `RS256`/`ES256` algorithms and required `exp`, `iat`, `iss`, `aud`, and `sub` claims. `X-User-ID` is accepted only in explicit non-production development mode; `X-Workspace-ID` selects a workspace but never bypasses membership.

Use `Idempotency-Key` on all state-changing POSTs. Cursor pagination and maximum graph limits are mandatory.

## Structural graph projection

`GET /repositories/{id}/graph` selects the latest published snapshot unless `snapshot_sha` is supplied. `limit` is capped at 1,000 nodes and `cursor` advances through stable kind/natural-key order. Edges on a page include only relationships whose endpoints are both present on that page; the response states this limitation whenever pagination is active.

Every projection returns the repository/snapshot IDs, pinned commit SHA, analysis version, source classes searched, node evidence IDs, edge evidence ID and confidence, parse/import diagnostics, and a next cursor. Unpublished or cross-workspace snapshots return `404` without revealing their existence.

## Inferred outputs and their versions

Every inferred result names the method that produced it so a reader can judge it:

| Output | Version | Notes |
|---|---|---|
| Bug-introducing commits | `szz-lite@1` | Lines removed by a fix, blamed on the parent; bulk and shallow-boundary links get lower confidence. |
| Flow answers ("how does X work?") | `code-flow@1` | Walks static CALLS/IMPORTS up to three hops plus inferred data-store/API use; every step cites its edge. Questions without a resolvable target fall back to retrieval. |
| Impact ranking | `impact-weighted@1` | 0.35 dependency + 0.30 co-change + 0.20 proximity + 0.15 bug correlation; weights fixed by the spec. |
| Defect labels compared | `szz-introducing@1` | Reported beside the deployed fix-touch label in `metrics.szz_labels`; not used to pick the deployed model. |
| Instability forecast | `instability-windowed-logreg@1` or `instability-gru@1` | The optional GRU (extra `deep`) is deployed only when its held-out average precision is higher. |
| Semantic similarity | `lsa` or `sentence-transformer:<model>` | Named in the genome response's limitations; optional extra `embeddings`, setting `CODE_GENOME_SEMANTIC_BACKEND`. |
| Readable docs | `docs-rewrite@1` | Model text is shown only when its citations match the deterministic source exactly. |
