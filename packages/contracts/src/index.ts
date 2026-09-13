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
  scope: Record<string, string>;
  limitations: string[];
  retrieval_version: string;
  created_at: string;
};
