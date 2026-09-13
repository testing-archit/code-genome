# Threat Model for CODE GENOME

**Generated:** 2026-09-13T18:05:00Z · **Version:** 2.0.0 · **Method:** STRIDE

## 1. System

CODE GENOME ingests untrusted GitHub repositories and produces tenant-scoped analysis/audit evidence. Next.js calls FastAPI; PostgreSQL stores records; Redis/ARQ dispatches workers; bare Git, PyDriller, and Tree-sitter parse without execution.

- **Web/API (HIGH):** `/api/v1/*` tenant UI and evidence APIs.
- **Git worker (CRITICAL):** ARQ private clone, history, and parsing jobs.
- **Auditor/intelligence (HIGH):** untrusted text and questions.
- **PostgreSQL/Redis (CRITICAL):** internal state and jobs.

Flow: browser→API→DB/queue→worker→GitHub/mirror→DB. Pinned IDs and workspace filters return evidence.

## 2. Trust Boundaries

- **Public:** health, GitHub responses, repository/report/question text.
- **Authenticated:** repository, graph, auditor, chat, retention, and export APIs.
- **Internal:** API↔PostgreSQL/Redis; worker↔GitHub/mirror.

Production auth is issuer/audience/signature-validated OIDC JWT plus DB membership. Development headers are rejected in production.

## 3. STRIDE

### Spoofing
Forged identity headers (**CRITICAL**): production requires OIDC; JWTs require `exp/iat/iss/aud/sub`; membership is checked. IdP configuration/key rotation remain deployment duties.

### Tampering
Crafted Git input (**HIGH**): GitHub HTTPS allowlist, validated refs, argument arrays, disabled hooks/config, bare mirrors, and bounded deletion paths. Prompt/report injection (**HIGH**): deterministic parsing/extractive retrieval; repository text is never executed or given tools.

### Repudiation
Credential, analysis, report, answer, retention, and export actions emit actor/request audit events. Production must ship append-only copies outside the application DB.

### Information Disclosure
Cross-tenant IDOR/secret leak (**CRITICAL**): workspace predicates and denial tests, AES-GCM credentials, ephemeral askpass, redacted Git errors. Add PostgreSQL RLS and managed KMS before multi-tenant production.

### Denial of Service
Request/report/Git time, output, file, byte, and history limits plus per-process throttling exist. Gateway/orchestrator must enforce aggregate rate, queue, CPU, memory, disk, and egress limits.

### Elevation of Privilege
Credential, retention, and audit exports require owner/admin; membership gates all protected routes.

## 4. Patterns

- Unsafe `db.get(Resource, id)` from HTTP; safe query includes `workspace_id`.
- Unsafe `subprocess.run(f"git {input}", shell=True)`; safe validated argument arrays/restricted environment.
- Unsafe JWT decode without algorithm/issuer/audience; safe JWKS key, fixed algorithms, required claims.
- Unsafe repository HTML rendering; safe React text nodes and attachment output.

## 5. Assumptions

1. TLS, OIDC login/session creation, KMS, and aggregate limits are deployment controls.
2. Workers have no host/Docker mounts and use restricted storage/egress.
3. MVP audit events are in the primary DB; production exports them to immutable storage.
