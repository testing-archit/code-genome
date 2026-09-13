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
| `GET /analyses/{id}` | job progress/diagnostics | `200 AnalysisRun` |
| `GET /repositories/{id}/inventory` | bounded refs, commits, and file manifest | `200 RepositoryInventory` |
| `GET /repositories/{id}/graph` | filtered snapshot graph | `200 GraphProjection` |
| `GET /evidence/{id}` | immutable source provenance locator | `200 Evidence` |
| `GET /repositories/{id}/impact` | rank impact for file/symbol/change | `200 ImpactResult` |
| `POST /delivery-reports` | create report + claims | `201 DeliveryReport` |
| `POST /delivery-reports/{id}/assessments` | queue verification | `202 AnalysisRun` |
| `GET /delivery-reports/{id}` | report, claims, assessments | `200 DeliveryReport` |
| `POST /chat/answers` | grounded Q&A | `200 GroundedAnswer` |

## Create delivery report

```json
{
  "repository_id": "repo_123",
  "text": "Updated Fixed Deposit cards and navigation.",
  "scope": {
    "from": "2026-09-08T00:00:00Z",
    "to": "2026-09-11T23:59:59Z",
    "branches": ["prod", "main"],
    "include_pull_requests": true,
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
- `429 RATE_LIMITED`; `503 ANALYSIS_DEGRADED`.

Use `Idempotency-Key` on all state-changing POSTs. Cursor pagination and maximum graph limits are mandatory.

## Structural graph projection

`GET /repositories/{id}/graph` selects the latest published snapshot unless `snapshot_sha` is supplied. `limit` is capped at 1,000 nodes and `cursor` advances through stable kind/natural-key order. Edges on a page include only relationships whose endpoints are both present on that page; the response states this limitation whenever pagination is active.

Every projection returns the repository/snapshot IDs, pinned commit SHA, analysis version, source classes searched, node evidence IDs, edge evidence ID and confidence, parse/import diagnostics, and a next cursor. Unpublished or cross-workspace snapshots return `404` without revealing their existence.
