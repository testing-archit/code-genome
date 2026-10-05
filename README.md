# CODE GENOME

> AI-powered software intelligence and engineering-audit platform.

CODE GENOME ingests a repository, builds a **Software Genome Graph** from code structure, code meaning, and Git evolution, then uses that evidence to reconstruct architecture, generate documentation, estimate risk and change impact, answer repository questions, and verify delivery reports.

All six MVP phases (0–5) are implemented. See [PILOT.md](PILOT.md) for the production launch gates, private-repository checklist, audit reproduction, retention, backup, and revocation drills.

## Why it exists

Undocumented systems are hard to change safely. Git knows how a system evolved, source code reveals its present structure, and delivery reports make claims about work performed. CODE GENOME joins these facts without presenting guesses as proof.

The four product engines are:

1. **Archaeologist** — architecture reconstruction, module discovery, and documentation.
2. **Risk Engine** — defect/regression likelihood based on repository history and change features.
3. **Impact Engine** — likely affected files/modules before a proposed change.
4. **Delivery Auditor** — compares natural-language delivery claims with commits, diffs, PRs, CI, and deployment evidence.

## Features

- **Repository genome**: files drawn as chromosome-style bands by module and hotspot; interactive dependency graph; file explorer with risk and impact per file; commit history with activity, authors, and ML intent labels.
- **Trained models** (`packages/ml`): commit-intent classification, temporal defect-proneness prediction (logistic regression, random forest, gradient boosting; also scored on SZZ "bug introduced" labels), change-impact link prediction and weighted ranking, hybrid BM25 + LSA retrieval, multi-signal module discovery, isolation-forest unusual-commit detection, and component instability forecasting. Every model is evaluated on held-out data against a baseline and shown on the **Models** page. See [ML_SPEC.md](ML_SPEC.md).
- **Chat**: multi-turn conversations whose answers cite the modules, files, hotspots, and commits they used. English, Hindi, and Hinglish, with dictation and read-aloud.
- **Voice agent**: Gemini 3.8 Live over a single-use ephemeral token (the API key never reaches the browser). The model must call the repository evidence tool before answering, and speaks English, Hindi, or Hinglish.
- **Project knowledge**: analysis stores redacted, line-cited excerpts of READMEs, docs, package manifests, and text source (any common language; vendored, build, and lock files skipped). Chat and voice answer "what does this project do?" from the repository's own README, route "if I change X what breaks?" to the impact engine (reporting missing files with the closest real paths), and answer code questions from source excerpts. Re-run analysis on an existing commit to backfill knowledge.
- **Voice brief**: each voice session embeds a deterministic brief (README summary, manifests, folders, file types, modules, latest commit) so the agent knows what "this project" means; specific answers still come from the cited evidence tool.
- **Streaming chat**: answers arrive over server-sent events from `GEMINI_MODEL` (default `gemini-3.8-flash`) and are stored only after every `[n]` citation maps to retrieved evidence; otherwise the extractive answer replaces the draft.
- **Change check**: paste a `git diff` or file list to rank downstream impact, risk, and touched modules.
- **Snapshot compare** and **exports**: diff two analysed snapshots; download architecture, risk, or comparison reports as cited Markdown or JSON (each export is audited).
- **Automatic analysis**: opt-in GitHub push webhook (`CODE_GENOME_GITHUB_WEBHOOK_SECRET`) with signature checks and replay protection.
- **Software Genome Graph**: one typed graph of files, components, functions and classes, developers, commits, packages, data stores, and external APIs with 16 relationship types (imports, calls, co-change, ownership, semantic similarity, bugs introduced and fixed, reads and writes). Every edge carries evidence; inferred ones are drawn dashed. Focus on a file or component for its 2-hop neighbourhood.
- **Bug history (SZZ-lite)**: fix commits traced back to the commits that likely introduced the bug, with blamed lines and confidence, per fix and per file. Labelled as candidates, not proof.
- **Evolution by component**: weekly or monthly lanes of churn, fixes, and bug-introducing commits per component, with the instability forecast, to see when a module became a hotspot.
- **Architecture by role**: components placed as entry points (API, UI), services, and data access, with detected data stores and integrations as their own nodes; the side panel shows why each role was assigned, contributors, and history.
- **"How does X work?"**: chat and voice follow the code graph from the named file, function, or component (calls, imports, data stores) and cite every step.
- **Readable docs**: optional Gemini rewrite of the six generated documents, kept only when every citation survives.
- **Optional model backends**: `uv sync --extra deep` adds a PyTorch GRU challenger for instability forecasting; `--extra embeddings` adds sentence-transformer similarity. Without them the lightweight models run and the results say so.
- **Search**, **delivery audit** with history, **private repository access**, **activity log**, command palette (⌘K), live analysis stages with counts, and light/dark (teal) themes.

Chat phrasing and voice use Gemini when `GEMINI_API_KEY` is set (`GEMINI_MODEL`, default `gemini-3.8-flash`; `GEMINI_LIVE_MODEL`, default `gemini-3.8-live`). Without a key, chat answers stay extractive and voice is unavailable; the models and search are unaffected.

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
# Set CODE_GENOME_CREDENTIAL_ENCRYPTION_KEY before connecting a private repository:
# openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
docker compose up --build
```

Open the dashboard at [http://localhost:3000](http://localhost:3000) and the API docs at [http://localhost:8000/docs](http://localhost:8000/docs). Compose provisions a development-only `Genome Lab` workspace and runs Postgres, Redis, the FastAPI service, the ARQ worker, and Next.js.

For local checks without containers:

```bash
npm install
uv sync --extra dev
npm run lint && npm run typecheck && npm run build
uv run ruff check services infra packages
uv run mypy services/api/code_genome_api services/worker/code_genome_worker packages/analyzers/code_genome_analyzers packages/genome/code_genome_genome packages/git/code_genome_git packages/ml/code_genome_ml
uv run pytest
# Run the API tests against Postgres instead of in-memory SQLite:
CODE_GENOME_TEST_DATABASE_URL=postgresql+psycopg://user@localhost/scratch_db uv run pytest services/api/tests
```

Structural analysis supports public repositories and private GitHub repositories through a read-only fine-grained PAT or GitHub App installation token. Credentials are AES-256-GCM encrypted with workspace/repository context and are supplied to Git only through an ephemeral askpass helper. Workers maintain locked bare mirrors, fetch branches incrementally, pin commit/tree SHAs, and publish bounded branch, commit, file-manifest, graph, and source-range evidence. Set a deployment-managed encryption key before enabling private access; the development header identity mode is not production authentication.

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
- Encrypted private-repository connections with owner/admin RBAC, revocation erasure, and audit events.
- Incremental worker-only bare mirrors plus branch, commit, and immutable file-manifest evidence.

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
