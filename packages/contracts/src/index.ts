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
