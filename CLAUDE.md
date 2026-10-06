@AGENTS.md

# CODE GENOME: working notes for Claude

## Keep this file current (standing rule)

This file is the single source of truth for the project's state. **Update it in the same
commit as any change that alters what is listed here**: a feature added or removed, an
endpoint, a model or analyzer version, a migration, a command, a configuration variable, a
known limitation, or the remaining-work list. Before ending a work session, check that
"Status", "Remaining work", and "Changelog" match reality (read `git log` if unsure), and add
a dated changelog line. Never record something as done that is not committed and verified.
If this file and the code disagree, trust the code, fix this file, and say so.

## Working agreements with the user

- Commit each completed step as you go, with a descriptive message ending in the
  `Co-Authored-By` line from the session's attribution reminder.
- Push the feature branch to `origin` (github.com/testing-archit/code-genome) after commits or
  merges. Never push to `main` or force-push without asking. Open a PR to `main` only when asked.
- When parallel agents work in git worktrees: merge their work, verify the merged content and
  that the worktree has no uncommitted changes, then remove the worktree and delete the branch.
  Never delete branches you did not create (e.g. Dependabot) or branches with unmerged work.
- Agents sharing the main working tree must commit only their own files by explicit path; never
  `git add -A` while another agent may have staged files (this once swept an agent's work into
  the wrong commit).
- Do not commit `apps/web/next-env.d.ts` (rewritten by `next dev`) or `.claude/` worktrees.
- The user writes in English, Hindi, and Hinglish; every user-facing answer path must support
  all three.

## What the product is

CODE GENOME ingests a GitHub repository, builds a Software Genome Graph from code structure,
meaning, and Git evolution, and answers two questions: "What does this system actually do?"
and "What will break if I change this file?". The authoritative product document is
`/Users/archit/Downloads/CODE_GENOME_Project_Document_Complete.docx` (14 sections, 12 use
cases, demo script); `PRD.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `API_SPEC.md`, and
`ML_SPEC.md` hold the engineering detail. Facts come from deterministic analysis and trained
models; Gemini only phrases cited evidence.

## Run it locally

Without Docker (SQLite, analysis jobs run inside the API process):

```bash
uv run alembic upgrade head
CODE_GENOME_BOOTSTRAP_DEMO=true uv run uvicorn code_genome_api.main:app --host 127.0.0.1 --port 8000 --reload
npm run dev            # web on http://localhost:3000, API docs on http://localhost:8000/docs
```

Full stack (Postgres, Redis, ARQ worker): `docker compose up --build`.
Dev identity headers: `X-Workspace-ID: ws_demo`, `X-User-ID: usr_demo`.

## Checks (run before every commit)

```bash
uv run ruff format services infra packages && uv run ruff check services infra packages
uv run mypy services packages            # CI runs this; it must be clean
uv run pytest -p no:warnings             # 208 tests at last update
npm run typecheck && npm run lint && npm test
npm run test:e2e --workspace @code-genome/web   # Playwright, mocked API
CODE_GENOME_TEST_DATABASE_URL=postgresql+psycopg://user@localhost/db uv run pytest services/api/tests  # Postgres
```

CI (`.github/workflows/ci.yml`): API + worker on SQLite with migration upgrade/rollback, the
same tests on Postgres 17, and the web app (lint, typecheck, unit, Playwright).

## Layout

- `services/api/code_genome_api/`: FastAPI app. `routes/` (HTTP only), `services/` (domain
  logic, no HTTP imports), `models.py`, `schemas.py`, `tools/benchmark.py`.
- `services/worker/`: ARQ worker (used when `CODE_GENOME_JOB_BACKEND=arq`).
- `packages/`: `analyzers` (Tree-sitter JS/TS and Python), `genome` (graph builder), `git`
  (bounded bare-mirror reader, SZZ), `evolution`, `intelligence`, `delivery-auditor`, `ml`
  (all models), `contracts` (TypeScript API types).
- `apps/web/`: Next.js 16 app. `components/` (shell, repo context, evidence drawer, component
  map, markdown, charts), `app/r/[id]/*` one folder per view, `lib/api.ts` client.
- `infra/migrations/versions/`: Alembic, latest `20261006_0015_historical_snapshots`.
- `docs/benchmarks/`: cross-project defect benchmark output.

## Core concepts and conventions

- **Snapshots** pin a commit; every derived row carries workspace, repository, snapshot, and
  analysis version. A snapshot built by an older analyzer is rebuilt in place, in one
  transaction, when the same commit is re-analysed (`CURRENT_GRAPH_VERSION` in
  `services/structural_analysis.py`); knowledge chunks and bug links are kept.
- **Evidence IDs**: `evidence:<provenance id>` (source ranges, knowledge excerpts, SZZ blame),
  `commit:<sha>`, `module:<id>`, `hotspot:<path>`, `change:<sha>:<path>`, provider signals.
  The web evidence drawer resolves each kind. Every answer cites; the empty answer is exactly
  `No supporting evidence was identified in the selected scope.`
- **Question routing** (`services/question_routing.py`, `services/flow.py`): overview
  questions use README/docs; "what breaks if I change X" uses the impact engine and names
  missing files with closest paths; "why is X risky" uses model factors and fix history; "how
  does X work" walks the genome graph. Everything else uses hybrid retrieval.
- **Health score** is a transparent heuristic with five shown components, not a quality claim.
- **Models abstain** (`insufficient_data`) rather than guess on thin history.
- Timestamps are stored as UTC; the web parses bare values as UTC (`lib/format.ts`).
- Frontend: histology palette (hematoxylin/eosin; dark theme teal/slate), grouped rail
  navigation Understand / Predict / Explain / Repository; overview is the hub.

## Feature inventory (mapped to the product document)

| Document section | Implemented as |
|---|---|
| §3 Stage 1 ingestion | GitHub URL registration, private repos via encrypted PAT, bounded bare mirrors, history, manifest; analysis as of a past date or commit (`as_of`) |
| §3 Stage 2, §6.1 code metrics | Tree-sitter JS/TS + Python: imports, exports, symbols, candidate CALLS, LOC, cyclomatic estimate, function count |
| §3 Stage 3 evolution | Co-change, churn, hotspots, contributors, commit intent model |
| §4 Software Genome Graph | `GET /genome`: files, components, symbols, developers, commits, packages, data stores, external APIs; 16 edge kinds incl. SEMANTICALLY_RELATED_TO, INTRODUCED_BUG, FIXED_BY, READS_FROM/WRITES_TO |
| §5.1 module discovery | 4 signals (deps, co-change, semantic, developer overlap); K-Means, DBSCAN, hierarchical, Louvain; silhouette, Davies-Bouldin, modularity, held-out lift; ablation; PCA map |
| §5.2 architecture | Component roles (api/service/data/ui/contract/…), data stores, integrations, layered explorer |
| §5.3 data flow, §7 "how does X work" | Graph-backed flow answers (`code-flow@1`), DATA_FLOW.md |
| §5.4, §8.6 generated docs | ARCHITECTURE, MODULES, DATA_FLOW, DEPENDENCIES, BUSINESS_LOGIC, RISK_REPORT; optional citation-checked Gemini rewrite |
| §6.1 regression risk | Temporal defect model, LR vs Random Forest vs Gradient Boosting, graph/social/code features, P/R/F1/ROC-AUC, optional TreeSHAP; cross-project benchmark |
| §6.2 change impact | Weighted score 0.35/0.30/0.20/0.15 with "Why?" panel incl. PageRank/betweenness; diff or file input; learned link model |
| §6.3 instability | Windowed logistic model; optional PyTorch GRU challenger deployed only if better |
| §7 AI layer | Grounded streaming chat (gemini-3.8-flash), Gemini Live voice (gemini-3.8-live) with repository brief, English/Hindi/Hinglish |
| §8.1 overview | Health dial, counts incl. contributors, high-risk components, instability forecast, two-question hub |
| §8.5 evolution timeline | Per-component churn/fixes/bug-introducing lanes |
| §10.3, §10.5 research | P@K, R@K, MAP@K for static vs co-change vs weighted vs learned |
| §10.4 SZZ | `szz-lite@1` bug links, Bug history view, SZZ labels compared with fix-touch labels |
| Use cases 9, 12 | Compare snapshots (drift), opt-in GitHub push webhook re-analysis |
| Delivery Auditor | Claims checked against commits, GitHub Actions checks, and Deployments |
| Search | Hybrid BM25 + LSA (optional sentence-transformers) over code, docs, commits |
| Exports | Cited Markdown/JSON reports, audited downloads |

## Versions (keep in sync with code)

Analyzer suite `tree-sitter-js-ts-py@0.3.0` (Python `tree-sitter-python@0.1.0`), graph
`structural-genome@0.2.0`, knowledge `knowledge@0.1.0`, SZZ `szz-lite@1`, SZZ labels
`szz-introducing@1`, genome `genome-graph@1`, flow `code-flow@1`, roles `roles-heuristic@1`,
timeline `timeline@1`, health `health-heuristic@2`, docs `generated-docs@1`, rewrite
`docs-rewrite@1`, provider evidence `provider-evidence@1`. Models: `defect-temporal@3`,
`defect-cross-project@1`, `modules-multisignal@2`, `cochange-linkpred-lr@1`,
`impact-weighted@1`, `instability-windowed-logreg@1`, `instability-gru@1`,
`commit-intent-tfidf-lr@1`, `commit-isolation-forest@1`, `hybrid-bm25-lsa-rrf@1`.

## Configuration

`.env.example` lists every variable. Key ones: `GEMINI_API_KEY`, `GEMINI_MODEL`
(gemini-3.8-flash; note it returns streamed output in one burst, gemini-3.5-flash streams
progressively), `GEMINI_LIVE_MODEL` (gemini-3.8-live; flash models cannot use the Live API),
`CODE_GENOME_CREDENTIAL_ENCRYPTION_KEY` (private repos), `CODE_GENOME_GITHUB_WEBHOOK_SECRET`,
`CODE_GENOME_GITHUB_API_TOKEN` (CI/deployment evidence), `CODE_GENOME_SEMANTIC_BACKEND`,
`CODE_GENOME_RATE_LIMIT_PER_MINUTE` (600). Optional extras: `deep` (torch GRU), `embeddings`
(sentence-transformers), `explain` (shap).

## Gotchas

- SQLite hides foreign-key ordering and time-zone bugs; run the Postgres tests for schema or
  query changes (foreign keys are deferred to commit, migration 0012).
- Tests share one process rate limiter; `conftest.py` resets it per test.
- `packages/contracts/src/index.ts` is append-heavy; parallel agents conflict at its end. When
  resolving, keep both sides and check the closing braces (`npm run typecheck`).
- Re-analysing three repositories at once saturates the single dev API process; poll gently.
- Commit-hour features use UTC since commit times were normalised.

## Status (2026-10-06)

Branch `feat/ml-models-voice-agent-ui`, pushed and in sync with `origin`. 208 Python tests,
5 web unit tests, Playwright e2e, typecheck, lint, and CI all pass. Three live repositories
are analysed with the current analyzer: sindresorhus/ky, testing-archit/ecocred,
testing-archit/code-genome. Every item in the product document's gap audit is implemented.

## Remaining work

1. Open a pull request from `feat/ml-models-voice-agent-ui` to `main` (waiting for the user).
2. Voice agent end-to-end check with a real microphone (needs the user).
3. Decide what to do with the untracked `deliverables/Code_Genome_Presentation.pptx` (3.6 MB):
   commit it, move it, or ignore it (ask the user).
4. Re-analyse the three live repositories so their snapshots include Python analysis
   (`tree-sitter-js-ts-py@0.3.0`); this matters most for code-genome, which is mostly Python.
5. Evaluation on real repositories is small (three live repos, six in the cross-project
   benchmark); impact P@K results on synthetic fixtures are near-tied and should not be read as
   real-world performance.

## Changelog

- 2026-10-06: CLAUDE.md created with project state, conventions, and this update rule.
  Removed five leftover empty agent worktrees/branches.
- 2026-10-06 (earlier): Python analysis; cross-project benchmark and TreeSHAP; CI on Postgres
  and Playwright; past-date snapshots; GitHub Actions/Deployments evidence for the auditor;
  genome graph, bug history, timeline, layered explorer, live progress, and Files metrics in
  the UI; Postgres foreign-key and UTC fixes; flow answers, SZZ labels, doc rewrites, GRU and
  embeddings backends; analyzer-version snapshot rebuilds; insights (health, explorer, docs);
  navigation and overview redesign; streaming chat; project knowledge for chat and voice;
  change check, compare, exports, webhook automation.
