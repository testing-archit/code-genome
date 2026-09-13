# Instructions for coding agents

## Product constraints

- Implement the JS/TS MVP before adding languages, graph databases, complex ML, or autonomous remediation.
- Prefer deterministic extraction over LLM inference. Treat LLM output as an explanation or candidate link, never as a repository fact.
- Preserve raw Git and provider evidence; derived facts must link to immutable source evidence and an analysis version.
- Keep implemented, merged, tested, and deployed as independently evidenced statuses.
- Never phrase an unsupported claim as fraud, lying, completion, correctness, or production release.

## Working method

1. Read the relevant document in `docs/` before changing an interface or schema.
2. Make a small vertical slice: migration/contracts, backend, worker, UI, tests, observability.
3. Add stable IDs, timestamps, repository SHA, and analysis-run ID to all derived records.
4. Validate untrusted inputs: Git URLs, webhook payloads, report text, branch names, file paths, and LLM JSON.
5. Run format, typecheck, unit tests, and the narrow integration test for changed behavior.

## Never hallucinate repository facts

- If evidence retrieval is empty, say exactly: `No supporting evidence was identified in the selected scope.`
- State the scope: repository, branch, date range, snapshot SHA, and sources searched.
- Show evidence links/IDs next to assertions. Label semantic similarity as inferred.
- Do not infer deployment from a merge, test success from code changes, or functional correctness from changed lines.
- Do not access another tenant/workspace’s repository, graph, embeddings, logs, or tokens.

## Code conventions

- FastAPI: Pydantic request/response models; domain services do not import HTTP objects.
- Python: type hints, UTC timestamps, structured logs, idempotent jobs.
- TypeScript: strict mode; generated types/contracts; accessible UI states.
- SQL: migrations only; tenant/workspace filters are mandatory.
- Graph writes: transactional replacement by repository snapshot; do not leave partial snapshots queryable.

## Definition of done

A feature is done only when its acceptance criteria pass, failure states are represented, evidence/audit records are created, access checks exist, and tests cover the happy path plus a meaningful failure path.
