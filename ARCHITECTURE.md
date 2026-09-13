# Architecture

## Logical design

```text
GitHub/webhooks + Git clone + CI/deployment providers
                  │
                  ▼
         ingestion queue / worker
                  │
  ┌───────────────┼────────────────┐
  ▼               ▼                ▼
Git mining    JS/TS analysis   provider evidence
  └───────────────┼────────────────┘
                  ▼
      versioned Software Genome Graph
                  │
    ┌─────────────┼──────────────┐
    ▼             ▼              ▼
Archaeologist  ML/Impact   Delivery Auditor/RAG
    └─────────────┼──────────────┘
                  ▼
        FastAPI contracts + Next.js UI
```

Correct the diagram’s intent in implementation: all analysis outputs are immutable snapshot-scoped records, not mutable global facts.

## Components

| Component | Responsibility | Technology |
|---|---|---|
| API | auth, CRUD, query endpoints, SSE/job status | FastAPI, Pydantic |
| Worker | clone/fetch, parsing, graph build, embeddings, report audits | Python, Celery/RQ/Arq + Redis |
| Relational store | tenants, runs, entities, evidence, reports, audit log | PostgreSQL |
| Object store | encrypted bare mirrors/diff artifacts where needed | S3-compatible |
| Graph layer | in-memory/query projections for MVP | NetworkX; Neo4j optional later |
| Frontend | dashboards, evidence drilldown, review | Next.js, TS, Tailwind |
| Integrations | GitHub App/API, GitPython, PyDriller, CI/deploy adapters | provider interfaces |

## Genome graph

Nodes: repository snapshot, file, symbol, module, commit, pull request, developer identity, test run, deployment, claim, evidence item.

Edges: `IMPORTS`, `EXPORTS`, `DECLARES`, `CALLS_CANDIDATE`, `CONTAINS`, `MODIFIED_IN`, `CO_CHANGED_WITH`, `MERGED_INTO`, `TESTED_BY`, `DEPLOYED_AS`, `SUPPORTS`, `CONTRADICTS`, `BELONGS_TO_MODULE`.

Edges carry confidence, provenance, source ranges when available, generator/analysis version, and snapshot ID. Never overwrite historical graph snapshots.

## Analysis pipeline

1. Validate repository authorization and create `analysis_run` with pinned refs.
2. Fetch/clamp repository size/time limits; collect commits and refs.
3. Parse changed/current JS/TS files; store syntax errors without failing the whole run.
4. Extract graph facts and calculate evolutionary metrics.
5. Build atomic snapshot graph and publish it only after validation.
6. Generate embeddings/indexes from permitted code/document fragments.
7. Compute candidate modules, risk/impact features, and summaries.

## Failure and recovery

- Bad repository/token: `AUTHORIZATION_FAILED`, no retry until credentials change.
- Rate limit/transient provider failure: exponential backoff with provider hints.
- Parse failure: partial success with per-file diagnostic; never manufacture relationships.
- Worker crash: idempotency key resumes/restarts un-published run.
- Model/embedding outage: structural features remain available; AI views show degraded mode.

## Scaling path

Begin with Postgres + NetworkX workers per snapshot. Move graph projections to Neo4j only after profiling demonstrates query/resource pressure; retain Postgres as audit/system-of-record.
