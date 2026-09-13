# Security

## Threat model priorities

Private source code, GitHub tokens, reports, embeddings, and audit findings are sensitive. Primary threats: cross-tenant access, credential leakage, malicious repository content, webhook forgery, prompt injection in code/report text, dependency compromise, and unsafe clone/analysis execution.

## Required controls

- Production startup requires OIDC mode. API bearer tokens are validated against JWKS with fixed algorithms and required issuer, audience, expiry, issued-at, and subject claims; development identity headers fail closed in production.
- Prefer GitHub App installation tokens scoped to selected repositories and read-only contents/metadata/PR checks. Rotate and revoke promptly; never log tokens or clone URLs with credentials.
- Encrypt integration credentials with KMS-managed envelope encryption. Store references, not plaintext, in Postgres.
- Enforce workspace membership/RBAC and tenant filters at query layer; add row-level security where practical. Test cross-tenant denial.
- Verify GitHub webhook signatures, use delivery IDs for replay protection, and fetch authoritative provider state instead of trusting payload claims.
- Clone/analyze untrusted repositories in restricted disposable workers: no host mounts, no Docker socket, no shell evaluation of repository files, CPU/memory/time/network egress limits.
- Parse rather than execute source. Treat README, code comments, commit messages, filenames, and reports as untrusted text for LLM prompts.
- Redact secrets from diffs/logs/chunks before model calls; default to no third-party model egress unless workspace policy permits it.
- Encrypt in transit and at rest; restrict object-store prefixes by workspace/repository.
- Maintain append-oriented audit events for credential changes, repository reads, report creation, assessments, exports, overrides, and admin actions.
- Enforce the built-in request/body limits and per-process throttle, plus an aggregate ingress limit for multi-replica deployments.

## Retention and deletion

Declare per-workspace retention for clones, patches, embeddings, reports, and audit records. Disconnect/revoke must stop future fetches and queue deletion of local mirrors/artifacts subject to legal retention policy. Backups need separate expiry coverage.

## Security response

Severity-1 credential/source exposure: revoke tokens, block integration, preserve minimal forensic audit records, notify authorized workspace contacts, rotate credentials, and document remediation. Never include sensitive source snippets in general incident notices.

## Pre-release checklist

- secret scanning, dependency scanning, SAST, container scanning, and signed build provenance;
- webhook signature/replay tests; SSRF/path traversal/archive bomb tests;
- authorization matrix and tenant-isolation tests;
- prompt-injection and data-egress tests;
- restore/deletion/revocation drill.
