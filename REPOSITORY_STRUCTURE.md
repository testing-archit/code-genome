# Recommended Repository Structure

```text
code-genome/
├── apps/
│   └── web/                         # Next.js + Tailwind
├── services/
│   ├── api/                         # FastAPI; HTTP/auth/orchestration only
│   └── worker/                      # asynchronous jobs and sandbox launcher
├── packages/
│   ├── analyzers/                   # Tree-sitter JS/TS extractors
│   ├── contracts/                   # OpenAPI-generated/shared TS/Python schemas
│   ├── delivery-auditor/            # claim extraction/matching/status rules
│   ├── genome/                      # nodes, edges, graph builders/projections
│   ├── git/                         # GitHub, GitPython, PyDriller adapters
│   ├── ml/                          # datasets, features, train/inference/eval
│   ├── rag/                         # chunking, retrieval, grounded generation
│   └── security/                    # redaction, policy, audit helpers
├── infra/
│   ├── compose/
│   ├── migrations/
│   └── deployment/
├── docs/                            # move this pack here
├── tests/
│   ├── fixtures/repos/              # synthetic Git fixtures only
│   ├── integration/
│   └── e2e/
├── scripts/                         # safe developer automation
├── .env.example
├── AGENTS.md
├── README.md
└── CONTRIBUTING.md
```

### Dependency direction

`apps/web → API contracts ← services/api`; `services/worker → domain packages`; domain packages must not depend on web or FastAPI. Provider adapters are behind interfaces so fixtures can run without network access. Keep database persistence adapters outside the core graph/claim scoring logic.
