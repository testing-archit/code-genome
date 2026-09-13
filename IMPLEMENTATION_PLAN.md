# Implementation Plan

## Phase 0 — foundation (1–2 weeks)

**Status: complete.**

Deliver monorepo, local compose, CI, auth/workspace model, migrations, job framework, observability, fixtures, and API contracts.

Acceptance: a tenant can create a repository record; every request is tenant-scoped; a fake analysis job transitions `QUEUED → RUNNING → SUCCEEDED/FAILED` and is visible in UI.

## Phase 1 — repository ingestion and structural genome (2–3 weeks)

**Status: complete.**

Implement GitHub App/token connection, clone/fetch, refs/commits/file manifests, Tree-sitter JS/TS parsing, import/export/symbol extraction, snapshot graph, and evidence drilldown.

Acceptance: fixture repositories produce a stable graph; malformed files are diagnosed but do not remove valid results; changing a commit creates a new immutable snapshot.

## Phase 2 — evolutionary intelligence and architecture (2–3 weeks)

**Status: complete.**

Implement PyDriller/GitPython history mining, co-change edges, hotspots, candidate module discovery, architecture pages, and automatic documentation drafts with citations.

Acceptance: seeded co-change fixture produces expected weighted edges; each generated module description declares its evidence and is labelled inferred where applicable.

## Phase 3 — Delivery Auditor MVP (3–4 weeks)

**Status: complete.**

Implement report parsing, atomic claim review, evidence retrieval/ranking, status rules, unreported-change detection, dashboard and downloadable audit report.

Acceptance: labelled sample reports render correct claim/source spans; every status has one or more evidence records or an explicit searched-scope explanation; claimed deployment cannot be verified without deployment evidence.

## Phase 4 — impact, risk, and grounded Q&A (3–4 weeks)

**Status: complete.**

Implement explainable baseline models, impact ranking, retrieval service, chat citations, feedback capture, and evaluation harness.

Acceptance: baseline beats documented trivial baseline on held-out fixture/history data or is reported as not useful; Q&A does not emit uncited repository assertions.

## Phase 5 — hardening and pilot (2 weeks)

**Status: complete.**

Threat-model review, load/error tests, retention controls, audit export, pilot onboarding, evaluation against human labels.

Acceptance: private-repo security checklist passes; restore/revocation tested; pilot users can reproduce an audit finding from source evidence.

## Sequencing rules

Do not start ML claims before deterministic data-quality tests and human-labelled evaluation data exist. Do not add Neo4j, broad language support, or “autonomous agent” features during MVP.
