# Testing Strategy

## Test pyramid

| Layer | Focus |
|---|---|
| Unit | parsers, graph transforms, scoring/status rules, auth policy |
| Contract | FastAPI OpenAPI/Pydantic and TypeScript contract compatibility |
| Integration | Postgres/Redis/worker/Git fixture/GitHub adapter fakes |
| End-to-end | repository connect → analysis → dashboard → report audit |
| Security | isolation, webhook validation, redaction, sandbox behavior |
| Evaluation | labelled claim and impact/risk datasets |

## Golden fixtures

Maintain small synthetic JS/TS repositories with known imports, renames, parse errors, co-change sequences, squash merge ancestry, failed/passed CI, deployments, external-only claims, and redacted-secret content. Keep expected graph/evidence snapshots versioned.

## Critical cases

- A malformed TypeScript file yields diagnostics and valid facts from other files.
- Incremental ingestion does not alter a published prior snapshot.
- Same job idempotency key produces one result.
- A claim with no matching diffs is not marked verified due only to a commit message.
- Merge does not imply deployed; test does not imply deployment.
- Semantic retrieval crossing tenant/repository scope is impossible.
- Unreported-change suppression requires actor/reason/audit event.
- Q&A answer with insufficient retrieval returns an evidence-gap response, not fabricated details.

## Quality gates

On every PR: format/lint/typecheck, unit tests, contract checks, dependency/security scans. On main: integration suite, e2e smoke, fixture regression snapshots, model-evaluation threshold check, and migration test from previous schema.

## Metrics

Track ingestion success/latency, parser coverage, graph validation failures, assessment coverage, human override rate, false-verification rate, citation coverage, queue failures, and access-denial anomalies. Alert on data isolation/security signals immediately.
