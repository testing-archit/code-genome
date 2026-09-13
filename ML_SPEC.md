# ML Specification

## Principle

ML prioritizes and explains; it does not certify repository facts. Deterministic extraction and provenance are prerequisites.

## Baseline models

| Capability | Inputs | MVP baseline | Output |
|---|---|---|
| Change impact | import/call graph distance, co-change, changed symbols, recency | weighted heuristic then logistic regression | affected entities + ranked rationale |
| Regression risk | churn, complexity, hotspot history, file age, recent defects | calibrated logistic regression / random forest | probability band + feature explanation |
| Claim evidence retrieval | report-claim text, paths, diffs, commits, PR descriptions | lexical BM25 + embeddings + metadata filters | ranked candidates |
| Claim assessment | retrieved evidence and rule features | deterministic status rules + calibrated classifier | status/confidence/limitations |

## Labels and evaluation

- Build human-labelled fixtures: claim ↔ evidence, status, materiality, and deployment state.
- Split by repository/time, never random records from the same commit into train/test.
- Report precision, recall, F1, calibration/Brier score, false-verification rate, coverage, and abstention rate.
- A claim model must optimize low false verification; abstain/`EXTERNAL_EVIDENCE_REQUIRED` where evidence is weak.

## Status rules

- `VERIFIED`: evidence meets claim facets and selected scope; semantic-only support cannot be sole evidence for high-confidence verification.
- `PARTIALLY_VERIFIED`: some discrete facets are evidenced, others missing/ambiguous.
- `NO_SUPPORTING_EVIDENCE`: searched sources produced no adequate support; not an accusation.
- `EXTERNAL_EVIDENCE_REQUIRED`: claim is principally design, CMS, infrastructure, manual QA, or otherwise outside connected sources.

## Embeddings/RAG safeguards

- Embed only approved repository contents; retain chunk path, SHA, range, access class, and index version.
- Filter retrieval by workspace, repository, authorized snapshot, and scope before vector similarity.
- Treat retrieved diff/path similarity as a candidate, not proof of completion.
- Store prompt template/model IDs and retrieval IDs; redact secrets before external model calls.
- Prevent model training on customer code unless a separate explicit opt-in is recorded.

## Retraining

Version datasets/features/models. Champion/challenger evaluation, rollback, drift monitoring, and human adjudication are required. No online learning from a single user correction without review.
