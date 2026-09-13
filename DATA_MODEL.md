# Data Model

## Tenancy and repository

| Entity | Key fields |
|---|---|
| `workspace` | id, name, created_at |
| `user`, `membership` | workspace_id, role, identity provider id |
| `repository` | workspace_id, provider, external_id, clone_url, default_branch, status |
| `repository_connection` | encrypted credential reference, scopes, installed_at, revoked_at |
| `branch_ref` | repository_id, name, head_sha, observed_at |
| `analysis_run` | repository_id, snapshot_sha, requested_refs, version, state, timestamps, error |
| `repository_snapshot` | repository_id, commit_sha, tree_sha, run_id, published_at |

## Genome and provenance

| Entity | Key fields |
|---|---|
| `graph_node` | snapshot_id, kind, natural_key, properties_json, source_locator |
| `graph_edge` | snapshot_id, type, from_node, to_node, weight, confidence, provenance_id |
| `provenance` | kind, provider_id, repository SHA, file path/range, extractor_version, observed_at |
| `commit` | repository_id, sha, parents, author identity, authored_at, message, stats |
| `file_change` | commit_sha, path, old_path, status, additions, deletions, patch locator |
| `pull_request` | provider id, head/base SHA, state, merged_at, url |
| `ci_run` | provider id, commit SHA, conclusion, workflow, url, finished_at |
| `deployment` | provider id, commit SHA/release, environment, status, occurred_at, url |

## Delivery Auditor

| Entity | Key fields |
|---|---|
| `delivery_report` | workspace_id, repository_id, raw_text (encrypted-at-rest policy), scope, submitted_by, version |
| `claim` | report_id, ordinal, original_span, normalized_text, claim_type, human_review_state |
| `evidence_item` | claim_id nullable, source_kind, source_id, relevance, entailment, provenance_id |
| `claim_assessment` | claim_id, status, confidence, rationale, scope_json, model/extractor versions |
| `unreported_change` | report_id, entity/provenance, materiality, explanation, review_state |
| `audit_event` | workspace_id, actor, action, resource, before/after hashes, request id, timestamp |

## Constraints and indexes

- Every business row has `workspace_id` directly or via an enforced parent relation.
- Unique: `(repository_id, commit_sha)`, `(snapshot_id, kind, natural_key)`, `(report_id, ordinal)`.
- Index evidence by `claim_id`, snapshot/commit SHA, timestamp, branch/ref, and source kind.
- Keep raw report text immutable; corrections create a new report version.
- Soft-delete metadata; retention jobs cryptographically erase permitted artifacts after policy expiry.
