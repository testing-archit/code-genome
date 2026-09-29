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

## Implemented models (`packages/ml`, `code_genome_ml`)

All models train per repository snapshot, in the worker right after publication and on
demand through `POST /repositories/{id}/ml/train`. Results are stored in `ml_model_runs`
with the snapshot SHA, model version, dataset description, and evaluation. A task that
cannot be evaluated honestly records `insufficient_data` and the product falls back to
the transparent baseline.

| Task | Method | Labels | Evaluation | Baselines |
|---|---|---|---|---|
| Commit intent | TF-IDF (identifier-aware words + char 3–5 grams) → multinomial logistic regression | 140 hand-labelled seed messages + Conventional Commits prefixes (stripped) | stratified 5-fold CV macro-F1, confusion matrix; seed-only model scored on the repo's prefixed commits | majority class |
| Defect-proneness | logistic regression (champion) vs histogram gradient boosting (challenger) over 11 history/graph features | file touched by a commit classified `fix` in the next period (SZZ-style, file level) | temporal split: train on [t0,t1)→[t1,t2), test on [t0,t2)→[t2,end]; ROC-AUC, average precision, precision/recall in top 20%, Brier, reliability curve, permutation importance | frequency + churn heuristic |
| Change impact | link prediction, logistic regression over import-graph (distance, common neighbours, Jaccard, Adamic-Adar) and prior co-change features | pair co-changes after a 70% time cutoff; 3:1 sampled negatives | grouped hold-out by source file; ROC-AUC, AP | prior co-change only; import distance only |
| Retrieval | BM25 + LSA (TF-IDF → truncated SVD) fused by reciprocal rank fusion | self-supervised: commit message → files it changed (commit text not indexed) | MRR, recall@10 per mode; the production mode is the per-repo champion | random ranking |
| Modules | Louvain communities on a weighted import + co-change graph | — | modularity | directory grouping modularity |
| Unusual commits | isolation forest over commit shape | — | flagged share (5% contamination) with robust z-score reasons | — |

Bulk commits (touching more than max(15, 20% of files)) are excluded from defect and
impact features. Hindi and Hinglish questions are handled by a bilingual lexicon in the
tokeniser; a Gemini rewrite of the *query* is only a fallback, and the resulting
evidence is labelled inferred. Gemini never trains, labels, or scores anything.
