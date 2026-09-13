# Threat Model for CODE GENOME

**Generated:** 2026-09-13T09:31:53Z  
**Version:** 1.0.0 · **Method:** STRIDE

## 1. System Overview

CODE GENOME ingests untrusted GitHub repositories and publishes tenant-scoped structural evidence. Next.js calls FastAPI; PostgreSQL stores tenant/evidence records; Redis/ARQ dispatches workers; bare Git and Tree-sitter process source.

| Component | Criticality | Entry point |
|---|---:|---|
| Web/API | HIGH | `/api/v1/*` |
| Git worker/credentials | CRITICAL | `analyze_repository` |
| PostgreSQL | CRITICAL | SQLAlchemy |
| Redis | HIGH | ARQ queue |

## 2. Trust Boundaries

- **Public:** health route, GitHub responses, repository/report text.
- **Authenticated:** repository, analysis, graph, evidence, connection routes.
- **Internal:** API↔database/queue and worker↔GitHub/database/mirror.

`X-User-ID` and `X-Workspace-ID` are development-only. Production requires issuer/audience-validated identity tokens.

## 3. STRIDE Analysis

### Spoofing

- Forged dev headers can impersonate a member (**CRITICAL**, `auth.py`). Membership checks limit discovery; production OIDC/JWT remains required.
- Stolen provider secrets grant repository access (**CRITICAL**). Encrypt at rest, prefer short-lived tokens, never return secrets.

### Tampering

- Crafted URL/ref/path can alter Git behavior (**HIGH**, `repository.py`). Require GitHub HTTPS URLs, validated refs, argument arrays, isolated config/hooks, no checkout, and bounded reads.
- Partial graph writes corrupt a snapshot (**HIGH**, `structural_analysis.py`). Keep commit/tree pins, stable IDs, constraints, and transactional publication.

### Repudiation

- Credential and analysis actions need an actor trail (**HIGH**). Append audit events for connect, revoke, and analysis dispatch.

### Information Disclosure

- Missing workspace predicates cause cross-tenant IDOR (**CRITICAL**). Scope every lookup and add denial tests; add PostgreSQL RLS before production.
- Clone URLs, process arguments, and errors can leak tokens (**CRITICAL**). Store credential-free URLs, use ephemeral askpass, and redact errors/logs.

### Denial of Service

- Repository/blob/queue exhaustion is **HIGH** risk. Enforce time, output, file and byte caps; deployment must add rate, queue, CPU, memory, and egress limits.

### Elevation of Privilege

- Non-owners could manage credentials (**HIGH**). Centralize role checks and restrict connection mutations to owner/admin roles.

## 4. Vulnerability Patterns

- **IDOR:** `db.get(Repository, id)` → query by both `id` and `workspace_id`.
- **Command injection:** `run(f"git {input}", shell=True)` → validated argument arrays.
- **Secret leak:** token in URL/log/database plaintext → encrypted record plus ephemeral askpass.
- **Auth spoofing:** trust identity headers in production → signed issuer/audience validation.

## 5. Assumptions & Accepted Risks

1. Header identity is limited to local/CI use.
2. Production workers have no host/Docker mounts and restrict resources/egress.
3. Public HTTPS cloning remains credential-free.
