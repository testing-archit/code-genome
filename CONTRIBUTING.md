# Contributing

## Before starting

Read `AGENTS.md`, the relevant design document, and existing contracts. Open an issue/design note for schema, authorization, provider, or model changes.

## Development rules

- Keep PRs focused and include migration/rollback notes for persisted-data changes.
- Use fixtures, never private customer repositories or code, in tests.
- Add provenance/audit behavior when adding a new conclusion, integration, or human override.
- Document evidence limitations in UI/API changes.
- Do not commit `.env`, tokens, raw cloned code, reports, or generated secrets.

## Pull request checklist

- [ ] Scope and acceptance criteria linked.
- [ ] Tests cover happy path and failure/degraded path.
- [ ] Tenant authorization and input validation reviewed.
- [ ] No source facts are generated without citations/provenance.
- [ ] API/contracts and docs updated.
- [ ] Migrations tested; logs/errors do not reveal credentials/source content.

## Commit style

Use short imperative titles, e.g. `feat(auditor): retain claim source spans`. Explain behavior and evidence/security implications in the PR description.

## Reporting vulnerabilities

Do not file public issues containing credentials or proprietary code. Use the project’s private security contact once configured; include affected version, reproduction steps, impact, and safe redactions.
