# CODE GENOME

> AI-powered software intelligence and engineering-audit platform.

CODE GENOME ingests a repository, builds a **Software Genome Graph** from code structure, code meaning, and Git evolution, then uses that evidence to reconstruct architecture, generate documentation, estimate risk and change impact, answer repository questions, and verify delivery reports.

## Why it exists

Undocumented systems are hard to change safely. Git knows how a system evolved, source code reveals its present structure, and delivery reports make claims about work performed. CODE GENOME joins these facts without presenting guesses as proof.

The four product engines are:

1. **Archaeologist** — architecture reconstruction, module discovery, and documentation.
2. **Risk Engine** — defect/regression likelihood based on repository history and change features.
3. **Impact Engine** — likely affected files/modules before a proposed change.
4. **Delivery Auditor** — compares natural-language delivery claims with commits, diffs, PRs, CI, and deployment evidence.

## MVP scope

- JavaScript and TypeScript repositories only.
- GitHub private/public repositories, branch-aware ingestion.
- Tree-sitter parsing; imports, exports, files, functions/classes, and conservative call references.
- NetworkX-backed graph; Postgres as the application system of record.
- FastAPI backend and Next.js/TypeScript/Tailwind frontend.
- Evidence-first dashboards, reports, and RAG Q&A.

Not in MVP: multi-language correctness guarantees, autonomous code changes, production deployment control, automatic claims of vendor dishonesty, or Neo4j as a required dependency.

## Quick start

The Phase 0 foundation requires Docker Compose. Start the full stack with:

```bash
cp .env.example .env
docker compose up --build
```

Open the dashboard at [http://localhost:3000](http://localhost:3000) and the API docs at [http://localhost:8000/docs](http://localhost:8000/docs). Compose provisions a development-only `Genome Lab` workspace and runs Postgres, Redis, the FastAPI service, the ARQ worker, and Next.js.

For local checks without containers:

```bash
npm install
uv sync --extra dev
npm run lint && npm run typecheck && npm run build
uv run ruff check services infra packages
uv run mypy services/api/code_genome_api services/worker/code_genome_worker packages/analyzers/code_genome_analyzers packages/genome/code_genome_genome packages/git/code_genome_git
uv run pytest
```

The current test analysis validates the durable `QUEUED → RUNNING → SUCCEEDED/FAILED` lifecycle only. It does not clone or inspect repository contents, and therefore never emits a snapshot SHA or repository facts. Structural ingestion starts in Phase 1.

## Foundation status

- Workspace membership boundary and non-leaking cross-tenant lookups.
- Validated, credential-free GitHub repository registration.
- Idempotent repository and analysis creation.
- Versioned SQL migration for workspaces, repositories, analysis runs, and idempotency records.
- Redis/ARQ worker path plus an inline development mode.
- Responsive repository dashboard with live job polling and explicit evidence limitations.
- Deterministic Tree-sitter extraction for JS, JSX, TS, and TSX with source-range diagnostics.
- Stable snapshot-scoped structural graph construction with evidence on every node and edge.
- Hardened bare-Git reader that pins commit/tree IDs and reads bounded source blobs without checkout.

## Evidence contract

Every UI/API conclusion must include: status, confidence, evidence IDs, evidence type, repository snapshot/commit SHA, analysis version, and limitations. A model may summarize or rank evidence; it must never invent files, commits, tests, PRs, deployments, or business behavior.

See [AGENTS.md](AGENTS.md), [PRD.md](PRD.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Repository layout

```text
apps/web/                    Next.js dashboard and chat UI
services/api/                FastAPI routes, auth, orchestration
services/worker/             background ingestion/analysis jobs
packages/contracts/          versioned API schemas/types
packages/genome/             graph model, builders, projections
packages/analyzers/          Tree-sitter JS/TS analysis
packages/git/                GitHub/GitPython/PyDriller adapters
packages/ml/                 feature extraction, training, inference
packages/rag/                evidence retrieval and grounded answers
packages/delivery-auditor/   claims, matching, scoring, report rendering
infra/                       compose, migrations, deployment manifests
docs/                        this documentation pack after bootstrap
tests/                       integration/e2e fixtures
```

## Delivery status vocabulary

- **Implemented**: relevant source-code evidence exists.
- **Merged**: evidence is reachable from the selected target branch.
- **Tested**: linked CI/test evidence passed for the revision.
- **Deployed**: trusted deployment evidence identifies the release/environment.

None implies the next. “No supporting repository evidence” is not “someone lied.”
