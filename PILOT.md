# Pilot and Production Runbook

## Launch gates

The application fails startup in `production` unless OIDC is enabled. Configure:

```text
CODE_GENOME_ENVIRONMENT=production
CODE_GENOME_AUTH_MODE=oidc
CODE_GENOME_OIDC_ISSUER=https://identity.example/
CODE_GENOME_OIDC_AUDIENCE=code-genome
CODE_GENOME_OIDC_JWKS_URL=https://identity.example/.well-known/jwks.json
```

Provision a managed PostgreSQL database, Redis, TLS ingress, KMS-delivered credential key, aggregate gateway rate limits, and an immutable audit sink. Do not enable `BOOTSTRAP_DEMO` in production. The web deployment must obtain the bearer token through the organization's OIDC session/BFF; never bake it into `NEXT_PUBLIC_*` variables.

## Private-repository security checklist

- [x] Only credential-free `https://github.com/{owner}/{repo}` URLs are accepted.
- [x] Fine-grained credentials require `contents:read`, are AES-256-GCM encrypted with workspace/repository context, and are never returned.
- [x] Only owners/admins can connect or revoke credentials.
- [x] Git receives secrets through a mode-0700 temporary askpass helper; arguments/errors are tested for leakage.
- [x] Hooks, global/system Git config, prompts, and `file://` protocol are disabled.
- [x] Refs, history, manifest, source bytes, process time, and output are bounded.
- [x] Workers parse source without executing it and use locked bare mirrors.
- [x] Revocation erases ciphertext and queues deletion of the bounded mirror path.
- [x] Cross-workspace denials, credential erasure, mirror deletion, and dependency scans are automated.

Deployment must additionally restrict worker CPU, memory, disk and egress; mount no host paths or Docker socket; scan/sign images; and store the encryption key in KMS rather than environment files.

## Reproduce an audit finding

1. Register and connect a repository, then run analysis for the pinned branch.
2. Record the published snapshot SHA.
3. Submit a delivery narrative and date range in **Delivery auditor**.
4. Open a claim's `change:{commit}:{path}` evidence ID.
5. Verify the commit with `git show {commit} -- {path}` in an authorized clone. The original claim's exact character offsets and the downloaded Markdown report provide the review trail.
6. Deployment or CI claims must remain `EXTERNAL_EVIDENCE_REQUIRED` until an authoritative provider is connected.

## Retention, backup, and revocation drills

- Preview retention with `POST /api/v1/workspaces/{id}/retention/run` and `{"dry_run":true}`. Have an owner execute with `false`; reports/answers expire after `CODE_GENOME_RETENTION_DAYS` while audit records remain.
- Export audit records before maintenance from `GET /api/v1/workspaces/{id}/audit-events?format=csv`.
- Back up PostgreSQL and the mirror volume under separate encrypted policies. Quarterly, restore into an isolated environment, run `alembic upgrade head`, and compare workspace/repository/snapshot counts and sampled evidence IDs.
- Revoke a private connection and verify ciphertext fields are empty, future fetches fail, the worker mirror disappears, and an audit event exists. Automated tests cover credential erasure and bounded mirror deletion.

## Pilot evaluation

Use `packages/intelligence/tests/fixtures/risk_labels.json` as the versioned reviewer-labelled baseline. CI requires its Brier score to beat a prevalence-only constant. Collect claim false-verification, abstention, citation coverage, unreported-change precision, and answer feedback by repository/time split before replacing deterministic baselines.
