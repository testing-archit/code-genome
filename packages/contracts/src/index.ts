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
  type: "DECLARES" | "EXPORTS" | "IMPORTS";
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
    kind: "file" | "module" | "hotspot" | "commit";
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
  }>;
  modules: Array<{ name: string; changed_files: number; impacted_files: number; inferred: boolean }>;
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
