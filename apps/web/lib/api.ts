import type {
  AnalysisRun,
  AnswerLanguage,
  Architecture,
  AuditEventRecord,
  Conversation,
  ConversationSummary,
  ConversationTurn,
  DeliveryReport,
  Evidence,
  GroundedAnswer,
  GraphProjection,
  ImpactAnalysis,
  MlOverview,
  ProblemDetail,
  Repository,
  RepositoryConnection,
  RepositoryInventory,
  RiskAnalysis,
  SearchResults,
  VoiceName,
  VoiceSession,
} from "@code-genome/contracts";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
export const workspaceId = process.env.NEXT_PUBLIC_WORKSPACE_ID ?? "ws_demo";
const userId = process.env.NEXT_PUBLIC_USER_ID ?? "usr_demo";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
  }
}

function requestHeaders(idempotencyKey?: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Workspace-ID": workspaceId,
    "X-User-ID": userId,
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${apiUrl}${path}`, {
      ...init,
      headers: { ...requestHeaders(), ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("The API is unreachable. Check that the API server is running.", 0, null);
  }
  if (!response.ok) {
    let problem: Partial<ProblemDetail> = {};
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      // Non-JSON error bodies fall back to the status text.
    }
    throw new ApiError(
      problem.detail || response.statusText || "The API request failed.",
      response.status,
      problem.code ?? null,
    );
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  listRepositories: () => request<Repository[]>("/repositories"),
  createRepository: (cloneUrl: string, branch: string) =>
    request<Repository>("/repositories", {
      method: "POST",
      headers: requestHeaders(crypto.randomUUID()),
      body: JSON.stringify({ clone_url: cloneUrl, default_branch: branch }),
    }),
  getConnection: (repositoryId: string) =>
    request<RepositoryConnection>(`/repositories/${repositoryId}/connection`),
  putConnection: (repositoryId: string, token: string) =>
    request<RepositoryConnection>(`/repositories/${repositoryId}/connection`, {
      method: "PUT",
      body: JSON.stringify({ token, token_kind: "fine_grained_pat", scopes: ["contents:read"] }),
    }),
  deleteConnection: (repositoryId: string) =>
    request<void>(`/repositories/${repositoryId}/connection`, { method: "DELETE" }),

  listAnalyses: (repositoryId: string) =>
    request<AnalysisRun[]>(`/repositories/${repositoryId}/analyses`),
  createAnalysis: (repositoryId: string, branch: string) =>
    request<AnalysisRun>(`/repositories/${repositoryId}/analyses`, {
      method: "POST",
      headers: requestHeaders(crypto.randomUUID()),
      body: JSON.stringify({ refs: [branch] }),
    }),
  getAnalysis: (runId: string) => request<AnalysisRun>(`/analyses/${runId}`),

  getGraph: (repositoryId: string, snapshotSha: string) =>
    request<GraphProjection>(
      `/repositories/${repositoryId}/graph?${new URLSearchParams({ snapshot_sha: snapshotSha, limit: "1000" })}`,
    ),
  getEvidence: (evidenceId: string) =>
    request<Evidence>(`/evidence/${encodeURIComponent(evidenceId)}`),
  getInventory: (repositoryId: string) =>
    request<RepositoryInventory>(
      `/repositories/${repositoryId}/inventory?commit_limit=500&file_limit=5000`,
    ),
  getArchitecture: (repositoryId: string) =>
    request<Architecture>(`/repositories/${repositoryId}/architecture?relationship_limit=200`),
  getRisk: (repositoryId: string) => request<RiskAnalysis>(`/repositories/${repositoryId}/risk`),
  getImpact: (repositoryId: string, path: string) =>
    request<ImpactAnalysis>(
      `/repositories/${repositoryId}/impact?${new URLSearchParams({ path })}`,
    ),

  ask: (repositoryId: string, question: string, language: AnswerLanguage, channel: "text" | "voice" = "text") =>
    request<GroundedAnswer>("/chat/answers", {
      method: "POST",
      body: JSON.stringify({ repository_id: repositoryId, question, language, channel }),
    }),
  rateAnswer: (answerId: string, rating: -1 | 1) =>
    request<void>(`/chat/answers/${answerId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    }),
  listConversations: (repositoryId: string) =>
    request<ConversationSummary[]>(`/repositories/${repositoryId}/conversations`),
  createConversation: (repositoryId: string) =>
    request<ConversationSummary>(`/repositories/${repositoryId}/conversations`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getConversation: (conversationId: string) =>
    request<Conversation>(`/conversations/${conversationId}`),
  sendMessage: (conversationId: string, content: string, language: AnswerLanguage) =>
    request<ConversationTurn>(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, language, channel: "text" }),
    }),
  deleteConversation: (conversationId: string) =>
    request<void>(`/conversations/${conversationId}`, { method: "DELETE" }),

  createVoiceSession: (repositoryId: string, voice: VoiceName, language: AnswerLanguage) =>
    request<VoiceSession>("/voice/sessions", {
      method: "POST",
      body: JSON.stringify({ repository_id: repositoryId, voice, language }),
    }),

  listDeliveryReports: (repositoryId: string) =>
    request<DeliveryReport[]>(`/delivery-reports?${new URLSearchParams({ repository_id: repositoryId })}`),
  createDeliveryReport: (repositoryId: string, text: string, from: string, to: string, branch: string) =>
    request<DeliveryReport>("/delivery-reports", {
      method: "POST",
      headers: requestHeaders(crypto.randomUUID()),
      body: JSON.stringify({
        repository_id: repositoryId,
        text,
        scope: {
          from: new Date(`${from}T00:00:00Z`).toISOString(),
          to: new Date(`${to}T23:59:59Z`).toISOString(),
          branches: [branch],
        },
      }),
    }),
  assessDeliveryReport: (reportId: string) =>
    request<DeliveryReport>(`/delivery-reports/${reportId}/assessments`, { method: "POST" }),
  downloadDeliveryReport: async (reportId: string) => {
    const response = await send(`/delivery-reports/${reportId}/download`);
    saveBlob(await response.blob(), `delivery-audit-${reportId}.md`);
  },

  getModels: (repositoryId: string) => request<MlOverview>(`/repositories/${repositoryId}/ml`),
  trainModels: (repositoryId: string) =>
    request<MlOverview>(`/repositories/${repositoryId}/ml/train`, { method: "POST" }),
  search: (repositoryId: string, query: string, kind?: string) => {
    const params = new URLSearchParams({ q: query, limit: "30" });
    if (kind) params.append("kind", kind);
    return request<SearchResults>(`/repositories/${repositoryId}/search?${params}`);
  },

  listAuditEvents: (limit = 200) =>
    request<AuditEventRecord[]>(
      `/audit-events?${new URLSearchParams({ workspace_id: workspaceId, format: "json", limit: String(limit) })}`,
    ),
  downloadAuditCsv: async () => {
    const response = await send(
      `/audit-events?${new URLSearchParams({ workspace_id: workspaceId, format: "csv" })}`,
    );
    saveBlob(await response.blob(), `audit-${workspaceId}.csv`);
  },
};

export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error && caught.message ? caught.message : fallback;
}
