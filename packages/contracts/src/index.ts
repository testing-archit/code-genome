export type Repository = {
  id: string;
  provider: "github";
  external_id: string;
  clone_url: string;
  default_branch: string;
  status: "REGISTERED";
  created_at: string;
};

export type AnalysisState = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";

export type AnalysisRun = {
  id: string;
  repository_id: string;
  snapshot_sha: string | null;
  requested_refs: string[];
  version: string;
  state: AnalysisState;
  progress: number;
  diagnostics: string[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_detail: string | null;
  /** Set when the run analysed a past point (full commit SHA or YYYY-MM-DD), not the head. */
  as_of?: string | null;
};

export type ProblemDetail = {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  request_id: string;
  code: string;
};

export type GraphNode = {
  id: string;
  kind: "FILE" | "SYMBOL" | "MODULE";
  natural_key: string;
  properties: Record<string, unknown>;
  evidence_ids: string[];
};

export type GraphEdge = {
  id: string;
  type: "DECLARES" | "EXPORTS" | "IMPORTS" | "CALLS";
  from_node: string;
  to_node: string;
  confidence: number;
  evidence_id: string;
};

export type GraphProjection = {
  scope: {
    repository_id: string;
    snapshot_id: string;
    snapshot_sha: string;
    analysis_version: string;
    sources: string[];
  };
  nodes: GraphNode[];
  edges: GraphEdge[];
  diagnostics: Array<{
    id: string;
    code: string;
    message: string;
    path: string;
    start_line: number | null;
    evidence_id: string | null;
  }>;
  next_cursor: string | null;
  limitations: string[];
};

export type Evidence = {
  id: string;
  kind: string;
  repository_sha: string;
  path: string;
  start_line: number | null;
  start_column: number | null;
  end_line: number | null;
  end_column: number | null;
  extractor_version: string;
  observed_at: string;
  heading?: string | null;
  excerpt?: string | null;
};

export type RepositoryInventory = {
  repository_id: string;
  snapshot_sha: string | null;
  refs: Array<{
    name: string;
    head_sha: string;
    observed_at: string;
  }>;
  commits: Array<{
    sha: string;
    parent_shas: string[];
    author_name: string;
    authored_at: string;
    message: string;
  }>;
  files: Array<{
    path: string;
    blob_sha: string;
    mode: string;
    size: number;
    analyzed: boolean;
  }>;
  limitations: string[];
};

export type Architecture = {
  repository_id: string;
  snapshot_sha: string;
  analysis_version: string;
  modules: Array<{
    id: string;
    name: string;
    file_paths: string[];
    confidence: number;
    description: string;
    citations: string[];
    inferred: boolean;
  }>;
  hotspots: Array<{
    path: string;
    commit_count: number;
    churn: number;
    score: number;
    citations: string[];
  }>;
  co_changes: Array<{
    left_path: string;
    right_path: string;
    commit_count: number;
    confidence: number;
    citations: string[];
  }>;
  limitations: string[];
};

export type DeliveryAssessment = {
  status: "VERIFIED" | "PARTIALLY_VERIFIED" | "NO_SUPPORTING_EVIDENCE" | "EXTERNAL_EVIDENCE_REQUIRED";
  confidence: number;
  rationale: string;
  evidence_ids: string[];
  limitations: string[];
  analysis_version: string;
  assessed_at: string;
};

export type DeliveryReport = {
  id: string;
  repository_id: string;
  raw_text: string;
  scope: {
    from: string;
    to: string;
    branches: string[];
    include_prs: boolean;
    include_ci: boolean;
    include_deployments: boolean;
  };
  submitted_by: string;
  parser_version: string;
  created_at: string;
  claims: Array<{
    id: string;
    ordinal: number;
    original_text: string;
    start_offset: number;
    end_offset: number;
    claim_type: string;
    assessment: DeliveryAssessment | null;
  }>;
  unreported_changes: Array<{
    path: string;
    evidence_ids: string[];
    materiality: number;
    explanation: string;
  }>;
  limitations: string[];
};

export type RiskAnalysis = {
  repository_id: string;
  snapshot_sha: string;
  scores: Array<{
    path: string;
    score: number;
    features: Record<string, number>;
    rationale: string;
    evidence_ids: string[];
    model_version: string;
  }>;
  limitations: string[];
};

export type ImpactAnalysis = {
  repository_id: string;
  snapshot_sha: string;
  selected_path: string;
  impacted: Array<{
    path: string;
    score: number;
    reasons: string[];
    evidence_ids: string[];
  }>;
  limitations: string[];
};

export type GroundedAnswer = {
  id: string;
  repository_id: string;
  question: string;
  answer: string;
  evidence_ids: string[];
  scope: {
    snapshot_id?: string;
    snapshot_sha?: string;
    analysis_version?: string;
    channel?: "text" | "voice";
    language?: AnswerLanguage;
    search_query?: string;
  };
  limitations: string[];
  retrieval_version: string;
  created_at: string;
};

export type AnswerLanguage = "auto" | "en" | "hi" | "hinglish";

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  channel: "text" | "voice";
  content: string;
  created_at: string;
  answer: GroundedAnswer | null;
};

export type ConversationSummary = {
  id: string;
  repository_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type Conversation = ConversationSummary & { messages: ConversationMessage[] };

export type ConversationTurn = {
  conversation: ConversationSummary;
  user_message: ConversationMessage;
  assistant_message: ConversationMessage;
};

export type VoiceName = "Kore" | "Puck" | "Charon" | "Aoede" | "Fenrir" | "Leda" | "Orus" | "Zephyr";

export type VoiceSession = {
  id: string;
  repository_id: string;
  snapshot_sha: string;
  model: string;
  voice: VoiceName;
  language: AnswerLanguage;
  websocket_url: string;
  token: string;
  expires_at: string;
  new_session_expires_at: string;
  setup: Record<string, unknown>;
  limitations: string[];
};

export type RepositoryConnection = {
  connected: boolean;
  connection_id: string | null;
  provider: string;
  token_kind: "fine_grained_pat" | "installation_token" | null;
  scopes: string[];
  key_version: string | null;
  installed_at: string | null;
  revoked_at: string | null;
};

export type AuditEventRecord = {
  id: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  before_hash: string | null;
  after_hash: string | null;
  request_id: string;
  created_at: string;
};

export type MlStatus = "trained" | "insufficient_data";

type Scores = { roc_auc: number; average_precision: number; precision_at_top20pct?: number; recall_at_top20pct?: number; brier?: number };

export type IntentResult = {
  model_version: string;
  status: MlStatus;
  dataset: { seed_version: string; seed_examples: number; weak_labels: number; class_counts: Record<string, number>; predicted_commits: number };
  metrics: {
    cv_folds: number;
    cv_accuracy: number;
    cv_macro_f1: number;
    majority_baseline_macro_f1: number;
    per_class_f1: Record<string, number>;
    confusion_matrix: { labels: string[]; matrix: number[][] };
    repository_holdout?: { examples: number; accuracy: number; macro_f1: number };
  };
  predictions: Array<{ sha: string; intent: string; probability: number; source: "conventional" | "model" }>;
  top_terms: Record<string, string[]>;
  training_seconds: number;
};

export type DefectResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  dataset: Record<string, unknown> & { train_positive?: number; train_negative?: number; test_positive?: number; test_negative?: number; train_period?: string[]; test_period?: string[]; bulk_commits_excluded?: number };
  metrics: { heuristic_baseline?: Scores; logistic_regression?: Scores; gradient_boosting?: Scores; test_base_rate?: number; note?: string };
  champion: "logistic_regression" | "gradient_boosting" | null;
  importance: Array<[string, number]>;
  calibration: { predicted?: number[]; observed?: number[] };
  predictions: Array<{ path: string; probability: number; band: "high" | "medium" | "low"; contributions: Array<[string, number]>; features: Record<string, number>; evidence_shas: string[] }>;
  feature_labels: Record<string, string>;
  training_seconds: number;
};

export type LinkResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  dataset: Record<string, unknown>;
  metrics: { model?: Scores; baseline_past_cochange?: { roc_auc: number }; baseline_import_distance?: { roc_auc: number }; test_pairs?: number; test_base_rate?: number; note?: string };
  coefficients: number[];
  feature_labels: Record<string, string>;
  training_seconds: number;
};

export type RetrievalResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  metrics: {
    status: string;
    queries: number;
    documents?: number;
    k?: number;
    bm25?: { mrr: number; recall_at_10: number };
    semantic?: { mrr: number; recall_at_10: number };
    hybrid?: { mrr: number; recall_at_10: number };
    random_baseline_recall?: number;
    selected_mode?: "hybrid" | "bm25" | "semantic";
  };
  training_seconds: number;
};

export type ModulesResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  metrics: { communities?: number; clustered_files?: number; isolated_files?: number; modularity_learned?: number; modularity_directory_baseline?: number; directory_groups?: number };
  modules: Array<{ name: string; files: string[]; cohesion: number; keywords: string[] }>;
  training_seconds: number;
};

export type AnomalyResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  metrics: { commits?: number; flagged?: number; contamination?: number };
  anomalies: Array<{ sha: string; score: number; reasons: string[] }>;
  training_seconds: number;
};

type Task<T> = { task: string; model_version: string; status: MlStatus; trained_at: string; result: T };

export type MlOverview = {
  repository_id: string;
  snapshot_sha: string;
  trained: boolean;
  tasks: Partial<{
    commit_intent: Task<IntentResult>;
    defect_risk: Task<DefectResult>;
    change_impact: Task<LinkResult>;
    retrieval: Task<RetrievalResult>;
    modules: Task<ModulesResult>;
    anomalies: Task<AnomalyResult>;
  }>;
  limitations: string[];
};

export type SearchResults = {
  query: string;
  snapshot_sha: string;
  model_version: string;
  hits: Array<{
    id: string;
    kind: "file" | "module" | "hotspot" | "commit" | "doc" | "code";
    title: string;
    path: string | null;
    score: number;
    bm25: number;
    semantic: number;
    bm25_rank: number | null;
    semantic_rank: number | null;
    match: "keyword and semantic" | "keyword" | "semantic (inferred)";
  }>;
  message: string | null;
  limitations: string[];
};

export type ChangeKind = "added" | "modified" | "deleted" | "renamed" | "listed";

export type ChangeImpact = {
  repository_id: string;
  snapshot_id: string;
  snapshot_sha: string;
  analysis_version: string;
  generated_at: string;
  summary: {
    changed_files: number;
    changed_in_snapshot: number;
    impacted_files: number;
    modules_touched: number;
    max_risk: number | null;
    high_risk_files: number;
  };
  changed: Array<{
    path: string;
    change: ChangeKind;
    previous_path: string | null;
    additions: number;
    deletions: number;
    in_snapshot: boolean;
    risk_score: number | null;
    risk_rationale: string | null;
    risk_model: string | null;
    modules: string[];
    evidence_ids: string[];
  }>;
  impacted: Array<{
    path: string;
    score: number;
    reasons: string[];
    evidence_ids: string[];
    via: string[];
    modules: string[];
    signals?: { dependency: number; co_change: number; proximity: number; bug_correlation: number } | null;
    weighted_score?: number | null;
    /** Import-graph position of the impacted file; context, not part of the weighted score. */
    graph_metrics?: ImpactGraphMetrics | null;
  }>;
  modules: Array<{ name: string; changed_files: number; impacted_files: number; inferred: boolean }>;
  /** People who recently changed the affected files (ownership@1); Git identities as recorded. */
  reviewers?: Array<{ name: string; commits: number; files: string[]; score: number; last_commit: string | null; evidence_ids: string[] }>;
  limitations: string[];
};

export type SnapshotSummary = {
  id: string;
  commit_sha: string;
  tree_sha: string;
  analysis_version: string;
  run_id: string;
  refs: string[];
  published_at: string;
  /** Set for a snapshot of a past point rather than the branch head. */
  as_of?: string | null;
};

export type ComparedFile = {
  path: string;
  base_blob_sha: string | null;
  head_blob_sha: string | null;
  size_delta: number;
};

export type ComparedImport = { source: string; target: string; evidence_id: string };

export type SnapshotComparison = {
  repository_id: string;
  base: SnapshotSummary;
  head: SnapshotSummary;
  generated_at: string;
  unavailable: Array<"files" | "imports" | "modules" | "hotspots">;
  counts: {
    files_added: number;
    files_removed: number;
    files_modified: number;
    imports_added: number;
    imports_removed: number;
    modules_changed: number;
  };
  files_added: ComparedFile[];
  files_removed: ComparedFile[];
  files_modified: ComparedFile[];
  imports_added: ComparedImport[];
  imports_removed: ComparedImport[];
  modules: Array<{
    name: string;
    status: "added" | "removed" | "changed";
    added_files: string[];
    removed_files: string[];
    inferred: boolean;
  }>;
  hotspots: Array<{ path: string; base_score: number | null; head_score: number | null; delta: number }>;
  limitations: string[];
};

export type ExportKind = "architecture" | "risk" | "comparison";
export type ExportFormat = "md" | "json";

export type RepositoryAutomation = {
  repository_id: string;
  auto_analyze: boolean;
  branch: string;
  webhook_configured: boolean;
  webhook_path: string;
  events: string[];
};

export type HealthBand = "Healthy" | "Moderate" | "At risk";

export type RepositoryOverview = {
  repository_id: string;
  snapshot_sha: string;
  analysis_version: string;
  summary: string | null;
  summary_evidence_id: string | null;
  health: {
    score: number;
    band: HealthBand;
    version: string;
    components: Array<{ key: string; label: string; score: number; maximum: number; value: string; detail: string }>;
  };
  counts: {
    files: number;
    source_files: number;
    commits: number;
    contributors: number;
    modules: number;
    internal_imports: number;
    external_packages: number;
    documents: number;
  };
  high_risk_modules: Array<{ name: string; risk: number; files: number; riskiest: string[]; inferred: boolean }>;
  riskiest_files: Array<{ path: string; score: number; rationale: string; evidence_ids: string[] }>;
  contributors: Array<{ name: string; commits: number }>;
  risk_model: string;
  /** Null when the instability model is untrained or abstained. */
  unstable_components?: UnstableComponent[] | null;
  limitations: string[];
};

export type ComponentNode = {
  name: string;
  files: number;
  risk: number | null;
  fan_in: number;
  fan_out: number;
  externals: string[];
  inferred: boolean;
  description: string;
  riskiest: string[];
  paths: string[];
};

export type ComponentLink = { source: string; target: string; imports: number; co_changes: number; evidence_ids: string[] };

export type ModuleGraph = {
  repository_id: string;
  snapshot_sha: string;
  nodes: Array<ComponentNode & ComponentNodeExtras>;
  links: ComponentLink[];
  limitations: string[];
};

export type DocRewrite = {
  model: string;
  rewrite_version: string;
  /** "accepted" only when the rewrite kept every citation and cited nothing new. */
  status: "accepted" | "rejected" | "skipped";
  reason: string | null;
  created_at: string;
};

export type GeneratedDocuments = {
  repository_id: string;
  snapshot_sha: string;
  version: string;
  generated_at: string;
  documents: Array<{
    name: string;
    description: string;
    markdown: string;
    /** Model-written readable version of `markdown`; null unless an accepted rewrite exists. */
    rewritten_markdown?: string | null;
    rewrite?: DocRewrite | null;
  }>;
  /** True when the API has a Gemini key, so POST /docs/rewrite can run. */
  rewrite_available?: boolean;
};

// ---- Defect model v2 (random-forest candidate) and component instability forecasting.

export type DefectChampion = "logistic_regression" | "random_forest" | "gradient_boosting";

/** defect-temporal@2: adds a random-forest candidate and states how contributions are computed. */
export type DefectResultV2 = Omit<DefectResult, "champion" | "metrics"> & {
  champion: DefectChampion | null;
  metrics: DefectResult["metrics"] & { random_forest?: Scores };
  /** What `predictions[*].contributions` mean for the chosen champion. */
  contribution_method?: string | null;
  contribution_unit?: "log-odds" | "probability" | null;
};

type InstabilityScores = { roc_auc: number; average_precision: number; brier: number; precision: number; recall: number; f1: number };

/** instability-windowed-logreg@1: logistic regression over the last `window` periods (not a recurrent network). */
export type InstabilityResult = {
  model_version: string;
  status: MlStatus;
  reason: string | null;
  dataset: Record<string, unknown> & {
    period_kind?: "week" | "commit_window";
    period_size?: number;
    periods?: number;
    window?: number;
    components?: number;
    label?: string;
    train_samples?: number;
    test_samples?: number;
    train_positive?: number;
    test_positive?: number;
    train_period?: string[];
    test_period?: string[];
    forecast_after?: string;
    bulk_commits_excluded?: number;
  };
  metrics: {
    threshold?: number;
    model?: InstabilityScores;
    baseline_persistence?: InstabilityScores;
    baseline_historical_rate?: InstabilityScores;
    /** Optional PyTorch GRU challenger on the same hold-out (instability-gru@1). */
    gru?: (InstabilityScores & { model_version: string }) | { model_version: string; status: "unavailable"; reason: string };
    champion?: "logistic_regression" | "gru";
    champion_rule?: string;
    test_base_rate?: number;
    note?: string;
  };
  coefficients: Array<[string, number]>;
  predictions: Array<{
    component: string;
    probability: number;
    band: "high" | "medium" | "low";
    files: number;
    /** [non-fix commits, bug-fix commits] per period, oldest to newest. */
    recent_periods: Array<[number, number]>;
    fixed_last_period: boolean;
    contributions: Array<[string, number]>;
    evidence_shas: string[];
  }>;
  feature_labels: Record<string, string>;
  contribution_method: string;
  training_seconds: number;
  champion?: "logistic_regression" | "gru";
};

export type MlOverviewWithInstability = Omit<MlOverview, "tasks"> & {
  tasks: Omit<MlOverview["tasks"], "defect_risk"> &
    Partial<{ defect_risk: Task<DefectResultV2>; instability: Task<InstabilityResult> }>;
};

/** Inferred forecast returned by GET /repositories/{id}/overview as `unstable_components`. */
export type UnstableComponent = {
  name: string;
  probability: number;
  band: "high" | "medium" | "low";
  files: number;
  fixed_last_period: boolean;
  evidence_ids: string[];
  model_version: string;
};

/** Optional overview field: null when the instability model is untrained or abstained. */
export type RepositoryOverviewInstability = { unstable_components?: UnstableComponent[] | null };

// ---- Multi-signal module discovery, richer defect features, weighted impact ranking.

export type ImpactGraphMetrics = { pagerank: number; pagerank_percentile: number; betweenness: number };

/** Components of the weighted impact score (impact-weighted@1), each in [0, 1]. */
export type ImpactSignals = { dependency: number; co_change: number; proximity: number; bug_correlation: number };

/** Each `ImpactAnalysis.impacted[*]` item with the optional weighted-score fields. */
export type ImpactItemWeighted = ImpactAnalysis["impacted"][number] & {
  signals?: ImpactSignals | null;
  /** 0.35·dependency + 0.30·co_change + 0.20·proximity + 0.15·bug_correlation. */
  weighted_score?: number | null;
  graph_metrics?: ImpactGraphMetrics | null;
};

export type ImpactAnalysisWeighted = Omit<ImpactAnalysis, "impacted"> & { impacted: ImpactItemWeighted[] };

export type ClusterAlgorithm = "louvain" | "agglomerative" | "kmeans" | "dbscan";

export type ClusterScores = {
  clusters: number;
  unassigned_share: number;
  silhouette: number | null;
  davies_bouldin: number | null;
  modularity: number;
  /** Share of file pairs changed together after the hold-out cut-off that share a cluster. */
  heldout_within_share: number | null;
  /** heldout_within_share divided by the chance rate for the same cluster sizes. */
  heldout_lift: number | null;
};

export type AblationVariant = "structure_only" | "structure_cochange" | "all_signals";

/** modules-multisignal@2. The older ModulesResult keys are kept and describe the champion. */
export type ModulesResultV2 = Omit<ModulesResult, "metrics"> & {
  metrics: ModulesResult["metrics"] & {
    champion?: ClusterAlgorithm;
    selection_rule?: string;
    algorithms?: Partial<Record<ClusterAlgorithm, ClusterScores & { mean_rank?: number; parameters: Record<string, unknown> }>>;
    directory_baseline?: ClusterScores;
    beats_directory_baseline?: Record<"silhouette" | "davies_bouldin" | "modularity" | "heldout_lift", boolean | null>;
    ablation?: Partial<Record<AblationVariant, ClusterScores & { signals: string[] }>>;
    ablation_algorithm?: ClusterAlgorithm;
    signals?: Array<{ name: string; label: string }>;
    heldout?: { cutoff: string | null; pairs: number; note: string };
    unassigned_files?: number;
    projection_explained_variance?: number[];
  };
  /** Inferred 2-D layout (PCA of the four-signal representation), at most 1500 files; `cluster` indexes `modules`, -1 = unassigned. */
  projection?: Array<{ path: string; x: number; y: number; cluster: number }>;
  projection_method?: "pca" | null;
};

export type DefectScoresV3 = {
  roc_auc: number;
  average_precision: number;
  precision_at_top20pct?: number;
  recall_at_top20pct?: number;
  brier?: number;
  precision?: number;
  recall?: number;
  f1?: number;
};

/** defect-temporal@3: graph centrality, ownership, optional code metrics; precision/recall/F1 at a training-period decision rule. */
export type DefectResultV3 = Omit<DefectResultV2, "metrics" | "dataset"> & {
  dataset: DefectResultV2["dataset"] & {
    features?: string[];
    label_source?: string;
    code_metrics?: { used: string[]; missing: string[]; files_measured: Record<string, number>; note: string };
  };
  metrics: {
    heuristic_baseline?: DefectScoresV3;
    logistic_regression?: DefectScoresV3;
    random_forest?: DefectScoresV3;
    gradient_boosting?: DefectScoresV3;
    test_base_rate?: number;
    decision_rule?: string;
    note?: string;
    /** The same candidates scored on SZZ-lite "bug introduced in the period" labels (comparison only). */
    szz_labels?: {
      label_version: string;
      label: string;
      links_supplied: number;
      links_used: number;
      train_positive: number;
      test_positive: number;
      status: "evaluated" | "insufficient_data";
      reason?: string;
      heuristic_baseline?: DefectScoresV3;
      logistic_regression?: DefectScoresV3;
      random_forest?: DefectScoresV3;
      gradient_boosting?: DefectScoresV3;
      test_base_rate?: number;
    };
  };
};

export type RankingApproach = "static_dependency" | "co_change" | "weighted" | "link_model" | "learned_weights";

export type RankingScores = {
  precision_at_5: number;
  recall_at_5: number;
  map_at_5: number;
  precision_at_10: number;
  recall_at_10: number;
  map_at_10: number;
};

export type ImpactRankingEvaluation = {
  status: "evaluated" | "insufficient_data";
  reason?: string;
  ks?: number[];
  queries?: number;
  test_commits?: number;
  periods?: { history_before: string; learned_labels: string[]; test_after: string };
  learned_training_pairs?: number;
  mean_pool_size?: number;
  approaches?: Partial<Record<RankingApproach, RankingScores>>;
  descriptions?: Partial<Record<RankingApproach, string>>;
  best_by_map_at_10?: RankingApproach;
  weights?: ImpactSignals;
  signal_definitions?: Record<keyof ImpactSignals, string>;
  learned_component_weights?: ImpactSignals | null;
  note?: string;
};

export type LinkResultV2 = LinkResult & {
  ranking_evaluation?: ImpactRankingEvaluation;
  weighted_score?: {
    weights: ImpactSignals;
    signal_labels: Record<keyof ImpactSignals, string>;
    signal_definitions: Record<keyof ImpactSignals, string>;
  };
};

export type MlOverviewV3 = Omit<MlOverviewWithInstability, "tasks"> & {
  tasks: Omit<MlOverviewWithInstability["tasks"], "defect_risk" | "modules" | "change_impact"> &
    Partial<{ defect_risk: Task<DefectResultV3>; modules: Task<ModulesResultV2>; change_impact: Task<LinkResultV2> }>;
};

// ---- Code metrics, candidate calls, SZZ bug links, genome graph, timeline, live progress.

/** Graph edge types including candidate CALLS (confidence < 1, resolved without type checking). */
export type GraphEdgeTypeV2 = GraphEdge["type"] | "CALLS";

/** FILE node `properties` written by structural-genome@0.2.0. */
export type FileNodeMetrics = {
  language: "javascript" | "typescript" | "tsx" | "python";
  content_sha256: string;
  parse_status: "COMPLETE" | "PARTIAL";
  /** Non-blank lines holding at least one non-comment token. */
  loc: number;
  /** 1 + decision points (if, loops, case, catch, ternary, &&, ||, ??). */
  complexity: number;
  /** Function, method, and arrow-function declarations. */
  functions: number;
  data_reads: number;
  data_writes: number;
  external_hosts: string[];
};

/** Fields added to GET /analyses/{id} for live progress. */
export type AnalysisStage =
  | "fetching"
  | "indexing"
  | "mining_history"
  | "evolution"
  | "tracing_bugs"
  | "parsing"
  | "publishing"
  | "complete";

export type AnalysisProgressCounts = Partial<{
  files_indexed: number;
  source_files: number;
  commits_mined: number;
  co_change_pairs: number;
  modules_discovered: number;
  fix_commits: number;
  bug_links_traced: number;
  files_parsed: number;
  dependencies_mapped: number;
  call_edges: number;
  knowledge_chunks: number;
}>;

export type AnalysisRunWithProgress = AnalysisRun & {
  stage?: AnalysisStage | null;
  progress_counts?: AnalysisProgressCounts;
};

export type ArchitectureRole =
  | "api"
  | "service"
  | "data"
  | "contract"
  | "ui"
  | "util"
  | "config"
  | "infra/scripts"
  | "test"
  | "unknown";

export type ComponentDatastore = {
  name: string;
  packages: string[];
  files: string[];
  reads: number;
  writes: number;
  access: "read" | "write" | "read_write" | "unknown";
  via: "direct" | "via import";
  evidence_ids: string[];
  inferred: true;
};

export type ComponentIntegration = {
  name: string;
  package: string | null;
  host: string | null;
  files: string[];
  evidence_ids: string[];
};

/** Optional fields added to GET /repositories/{id}/module-graph nodes. */
export type ComponentNodeExtras = {
  role?: ArchitectureRole;
  role_signal?: string;
  datastores?: ComponentDatastore[];
  integrations?: ComponentIntegration[];
  contributors?: Array<{ name: string; commits: number; share: number }>;
  commits?: number;
  bug_fixes?: number;
  last_changed?: string | null;
  /** Smallest number of people who made half of the component's commits; null if quiet. */
  bus_factor?: number | null;
};

export type GenomeNodeKind =
  | "file"
  | "component"
  | "function"
  | "class"
  | "developer"
  | "commit"
  | "external"
  | "datastore"
  | "external_api";

export type GenomeEdgeKind =
  | "IMPORTS"
  | "CALLS"
  | "DECLARES"
  | "DEPENDS_ON"
  | "BELONGS_TO_MODULE"
  | "CO_CHANGED_WITH"
  | "MODIFIED_BY"
  | "AUTHORED_BY"
  | "OWNED_BY"
  | "SEMANTICALLY_RELATED_TO"
  | "INTRODUCED_BUG"
  | "FIXED_BY"
  | "READS_FROM"
  | "WRITES_TO"
  | "USES_DATASTORE"
  | "CALLS_API";

export type GenomeNode = {
  /** "file:<path>", "component:<name>", "symbol:<graph node id>", "developer:<hash>", "commit:<sha>", "external:<pkg>", "datastore:<name>", "external_api:<name>". */
  id: string;
  kind: GenomeNodeKind;
  label: string;
  properties: Record<string, unknown>;
  evidence_ids: string[];
  inferred: boolean;
};

export type GenomeEdge = {
  id: string;
  kind: GenomeEdgeKind;
  source: string;
  target: string;
  weight: number;
  confidence: number;
  inferred: boolean;
  /** "evidence:<provenance id>" or "commit:<sha>". */
  evidence_ids: string[];
};

/** GET /repositories/{id}/genome?focus=&limit= */
export type GenomeGraph = {
  scope: {
    repository_id: string;
    snapshot_id: string;
    snapshot_sha: string;
    analysis_version: string;
    sources: string[];
  };
  version: string;
  focus: string | null;
  focus_node_id: string | null;
  nodes: GenomeNode[];
  edges: GenomeEdge[];
  node_counts: Partial<Record<GenomeNodeKind, number>>;
  edge_counts: Partial<Record<GenomeEdgeKind, number>>;
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
  limitations: string[];
};

export type BugIntroduction = {
  introducing_sha: string;
  subject: string | null;
  author: string | null;
  authored_at: string | null;
  path: string;
  lines: number;
  confidence: number;
  evidence_id: string;
  evidence: {
    fix_sha: string;
    parent_sha: string;
    path: string;
    fix_removed_ranges: Array<[number, number]>;
    blamed_ranges: Array<[number, number]>;
    bulk_introducing_commit: boolean;
    shallow_boundary: boolean;
  };
  bulk_commit: boolean;
  shallow_boundary: boolean;
};

/** GET /repositories/{id}/bugs?path=&limit= (SZZ-lite candidates, not proof). */
export type BugHistory = {
  repository_id: string;
  snapshot_sha: string;
  analysis_version: string;
  fix_rule: string;
  path: string | null;
  fixes: Array<{
    fix_sha: string;
    subject: string | null;
    author: string | null;
    authored_at: string | null;
    files: string[];
    introducing: BugIntroduction[];
  }>;
  files: Array<{
    path: string;
    fix_commits: number;
    introducing_commits: number;
    fix_shas: string[];
    introducing_shas: string[];
    lines: number;
  }>;
  counts: { fix_commits: number; bug_links: number; files: number };
  limitations: string[];
};

export type TimelinePoint = {
  /** ISO date (Monday for weeks, first day for months), UTC. */
  bucket_start: string;
  commits: number;
  churn: number;
  fix_commits: number;
  authors: number;
  bug_introducing_commits: number;
};

/** GET /repositories/{id}/timeline?bucket=week|month&path= */
export type EvolutionTimeline = {
  repository_id: string;
  snapshot_sha: string;
  version: string;
  bucket: "week" | "month";
  path: string | null;
  buckets: string[];
  overall: TimelinePoint[];
  components: Array<{ name: string; role: ArchitectureRole | string; points: TimelinePoint[] }>;
  limitations: string[];
};

/** GET /delivery-reports/signals/{id}: CI or deployment evidence read from GitHub. */
export type ProviderSignal = {
  id: string;
  repository_id: string;
  provider: string;
  kind: "ci_run" | "deployment";
  commit_sha: string;
  name: string;
  /** Check-run conclusion (success, failure, ...) or latest deployment state. */
  outcome: string;
  environment: string | null;
  url: string | null;
  observed_at: string | null;
  fetched_at: string;
  analysis_version: string;
};
