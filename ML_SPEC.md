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
| Defect-proneness (`defect-temporal@3`) | logistic regression vs random forest (balanced class weights) vs histogram gradient boosting over 14 history/graph features (commits, churn, contributors, earlier fixes, recency, age, co-change degree, fan-in/out, size, import-graph betweenness and PageRank, top-author ownership share) plus up to 3 code metrics (`loc`, `complexity`, `functions` from FILE node properties; a metric no file carries is dropped and `dataset.code_metrics` says so); champion by held-out average precision, ties to the simpler model. Explanations describe the champion: log-odds contributions for logistic regression, a local reset-to-median probability attribution for tree ensembles (`contribution_method` in the result) | file touched by a commit classified `fix` in the next period (SZZ-style, file level) | temporal split: train on [t0,t1)→[t1,t2), test on [t0,t2)→[t2,end]; ROC-AUC, average precision, precision/recall in top 20%, precision/recall/F1 when flagging the training-period base rate of files, Brier, reliability curve, permutation importance. Snapshot code metrics and imports are applied to earlier cut-offs (slightly anachronistic) | frequency + churn heuristic |
| Component instability (`instability-windowed-logreg@1`) | windowed sequence model: logistic regression over the last 4 periods of per-component non-fix commits, fix commits, log churn, and authors, plus fix rate so far and size. Not a recurrent network. Components are directory groups (`group_components`); periods are calendar weeks when there are ≥3 commits/week, otherwise equal commit-count windows | component touched by a commit classified `fix` in the next period | temporal hold-out of the latest 25% of target periods; ROC-AUC, average precision, Brier, precision/recall/F1 at a threshold chosen on training data | persistence (next period = current period); historical fix rate |
| Change impact | link prediction, logistic regression over import-graph (distance, common neighbours, Jaccard, Adamic-Adar) and prior co-change features | pair co-changes after a 70% time cutoff; 3:1 sampled negatives | grouped hold-out by source file; ROC-AUC, AP | prior co-change only; import distance only |
| Change-impact ranking (`impact-weighted@1`) | explainable weighted score 0.35·dependency + 0.30·co-change + 0.20·proximity + 0.15·bug correlation (definitions below), returned per impact item as `signals` + `weighted_score` | files changed in the same later commit as the query file | commits after t2 (70%) are queries, ranked with history before t2; learned rankers fit on [t1=50%, t2) pairs. Precision@K, Recall@K, MAP@K (K = 5, 10) | static dependencies only; co-change only; learned link model; logistic regression over the four components |
| Modules (`modules-multisignal@2`) | four-signal file representation (shared import neighbours, co-change, LSA over path tokens and symbol names, developer overlap); K-Means (k by silhouette), DBSCAN (eps at the k-distance knee), Ward agglomerative (k by silhouette), Louvain on the import + co-change graph; champion by mean rank; PCA 2-D projection (≤1500 files) | — | silhouette and Davies-Bouldin in the four-signal space, modularity on the import + co-change graph, held-out co-change lift (signals rebuilt before a 70% time cut-off; share of later co-changing pairs inside one cluster ÷ chance); signal ablation structure → +co-change → all four | directory grouping |
| Retrieval | BM25 + LSA (TF-IDF → truncated SVD) fused by reciprocal rank fusion | self-supervised: commit message → files it changed (commit text not indexed) | MRR, recall@10 per mode; the production mode is the per-repo champion | random ranking |
| Unusual commits | isolation forest over commit shape | — | flagged share (5% contamination) with robust z-score reasons | — |

### Weighted impact components (`code_genome_ml.impact_ranking`)

Each component is in [0, 1] and computed for a target file T and candidate C:

* **dependency strength**: 1 for a direct import in either direction, 0.5 at two directed
  hops, 0.25 at three, else 0;
* **co-change strength**: commits that changed both / commits that changed T (confidence);
* **graph proximity**: 2 / (1 + shortest undirected import distance), 0 beyond 3 hops. This
  is the spec's 1/(1+d) rescaled by 2 so a direct neighbour scores 1;
* **historical bug correlation**: bug-fix commits touching both / bug-fix commits touching T.
  Fixes come from the intent model; when it is untrained, a fix-keyword rule is used and
  the impact response says so in `limitations`.

The weights are fixed, not learned. The ranking evaluation reports a logistic regression
over the same components (`learned_component_weights`) for comparison only.

### Module discovery selection rule

Algorithms are ranked on silhouette (higher), Davies-Bouldin (lower), modularity (higher)
and held-out co-change lift (higher); the lowest mean rank wins, ties go to Louvain, then
agglomerative, K-Means, DBSCAN. A clustering with fewer than two clusters or more than 20%
of files unassigned (DBSCAN noise) is not eligible. Silhouette and Davies-Bouldin are
computed in the four-signal space and therefore favour representation-based clustering;
modularity favours Louvain; held-out lift is the neutral check. The ablation reruns the
best representation-based algorithm on structure only, structure + co-change, and all
four signals with the same yardsticks. The legacy result keys (`communities`,
`modularity_learned`, …) now describe the selected champion.

Bulk commits (touching more than max(15, 20% of files)) are excluded from defect,
impact, ranking, and module features. Hindi and Hinglish questions are handled by a bilingual lexicon in the
tokeniser; a Gemini rewrite of the *query* is only a fallback, and the resulting
evidence is labelled inferred. Gemini never trains, labels, or scores anything.
