# Product Requirements Document

## Vision

CODE GENOME makes complex repositories explainable and delivery claims auditable through a single evidence-backed Software Genome Graph.

## Users

| User | Primary need | Outcome |
|---|---|---|
| Engineer | Understand an unfamiliar codebase and assess a change | architecture, dependencies, impact evidence |
| Engineering lead | Prioritize risky work | risk signals and traceable rationale |
| Founder/product owner | Validate outsourced delivery reports | claim-by-claim evidence and unreported changes |
| Auditor | Reconstruct what changed and when | immutable evidence trail |

## Jobs to be done

1. Given a JS/TS repository, map files, symbols, imports, modules, and historical co-change.
2. Explain how a component/module fits the system, using citations to source and history.
3. Given a file or PR, rank likely downstream impact with clear limitations.
4. Given a report and scope, assess each atomic claim as verified, partially verified, unsupported, or needs external evidence.
5. Surface material repository changes not represented in the report.

## Functional requirements

### Repository intelligence

- Connect GitHub repositories using installation tokens or least-privilege repository access.
- Clone/fetch safely; record branch heads and commits.
- Parse JS/TS source with Tree-sitter and capture structural facts plus parse diagnostics.
- Build a versioned graph with structural, semantic, and evolutionary edges.
- Discover candidate modules using directory, import, semantic, and co-change signals; label results as inferred.

### Delivery Auditor

- Accept pasted report text, date range, branches, optional PR/deployment sources.
- Extract atomic, reviewable claims; retain original source spans.
- Retrieve and score evidence from changes, PRs, CI, deployments, and graph context.
- Return per-claim status: `VERIFIED`, `PARTIALLY_VERIFIED`, `NO_SUPPORTING_EVIDENCE`, or `EXTERNAL_EVIDENCE_REQUIRED`.
- Identify unreported changes with configurable materiality thresholds.
- Support human override/annotation without altering raw evidence.

### AI Q&A

- Retrieve evidence before generation; cite result IDs and scope.
- Refuse to answer as fact when retrieval is insufficient.
- Persist answer, prompt template version, retrieval set, model version, and feedback.

## Non-functional requirements

- Multi-tenant isolation, encrypted credentials, auditable reads/actions.
- Idempotent asynchronous analysis with resumable progress.
- Typical MVP target: analyze a 10k-file repository incrementally; no synchronous request waits for full analysis.
- Accessible dashboard; every status has explanation and error/retry state.

## Success measures

- ≥90% of sample report claims result in an evidence-linked assessment.
- Human-labelled claim verification precision/recall are measured before marketing accuracy claims.
- 100% of generated answers show scope and citations, or explicitly report insufficient evidence.

## Out of scope

- Employee surveillance/scoring.
- Declaring legal/commercial breach.
- Proof that a visual/design-only or external-service task occurred without imported evidence.
- Automated merge, deployment, or code change actions.
