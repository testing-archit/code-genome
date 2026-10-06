import type {
  AnalysisRun,
  AnswerLanguage,
  Architecture,
  AssistantAnswer,
  AuditEventRecord,
  BugHistory,
  ChangeImpact,
  Conversation,
  ConversationSummary,
  ConversationTurn,
  DeliveryReport,
  Evidence,
  EvolutionTimeline,
  ExportFormat,
  ExportKind,
  GroundedAnswer,
  GeneratedDocuments,
  GenomeGraph,
  GraphProjection,
  ImpactAnalysis,
  MlOverview,
  ModuleGraph,
  ProblemDetail,
  ProviderSignal,
  PullRequestImpact,
  Repository,
  RepositoryAutomation,
  RepositoryConnection,
  RepositoryOverview,
  RepositoryInventory,
  RiskAnalysis,
  SearchResults,
  SnapshotComparison,
  SnapshotSummary,
  VoiceName,
  VoiceSession,
} from "@code-genome/contracts";

export const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
export const workspaceId = process.env.NEXT_PUBLIC_WORKSPACE_ID ?? "ws_demo";
const userId = process.env.NEXT_PUBLIC_USER_ID ?? "usr_demo";

export type StreamHandlers = {
  onDelta: (text: string) => void;
  onStatus?: (model: string) => void;
  onFallback?: () => void;
  signal?: AbortSignal;
};

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
    throw new ApiError(describeProblem(problem, response), response.status, problem.code ?? null);
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
  // Revoking synchronously can cancel the download in Safari and older Firefox.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type FieldError = { location?: Array<string | number>; message?: string };

/** Turn an RFC 9457 problem into a sentence that names the rejected field. */
function describeProblem(problem: Partial<ProblemDetail> & { errors?: FieldError[] }, response: Response): string {
  const detail = problem.detail || response.statusText || "The API request failed.";
  if (response.status === 413) return "That is too large to send. Shorten it and try again.";
  if (response.status === 429) return "Too many requests in the last minute. Wait a moment and try again.";
  if (!Array.isArray(problem.errors) || problem.errors.length === 0) return detail;
  const fields = problem.errors.slice(0, 3).map((error) => {
    const field = (error.location ?? []).filter((part) => part !== "body" && part !== "query").join(".");
    const message = (error.message ?? "is invalid").replace(/^Value error, /, "");
    return field ? `${field}: ${message}` : message;
  });
  return `${detail} ${fields.join("; ")}`;
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
  createAnalysis: (repositoryId: string, branch: string, asOf?: string) =>
    request<AnalysisRun>(`/repositories/${repositoryId}/analyses`, {
      method: "POST",
      headers: requestHeaders(crypto.randomUUID()),
      body: JSON.stringify({ refs: [branch], ...(asOf ? { as_of: asOf } : {}) }),
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

  checkChange: (repositoryId: string, diff: string, paths: string[]) =>
    request<ChangeImpact>(`/repositories/${repositoryId}/impact/change`, {
      method: "POST",
      body: JSON.stringify({ diff: diff.trim() ? diff : null, paths }),
    }),
  listSnapshots: (repositoryId: string) =>
    request<SnapshotSummary[]>(`/repositories/${repositoryId}/snapshots`),
  compareSnapshots: (repositoryId: string, base: string, head: string) =>
    request<SnapshotComparison>(
      `/repositories/${repositoryId}/compare?${new URLSearchParams({ base, head })}`,
    ),
  downloadExport: async (
    repositoryId: string,
    kind: ExportKind,
    format: ExportFormat,
    range?: { base: string; head: string },
  ) => {
    const params = new URLSearchParams({ format, ...(range ?? {}) });
    const response = await send(`/repositories/${repositoryId}/exports/${kind}?${params}`);
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${kind}.${format}`;
    saveBlob(await response.blob(), filename);
  },
  getOverview: (repositoryId: string) => request<RepositoryOverview>(`/repositories/${repositoryId}/overview`),
  getModuleGraph: (repositoryId: string) => request<ModuleGraph>(`/repositories/${repositoryId}/module-graph`),
  getGenome: (repositoryId: string, focus: string | null, limit = 400) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (focus) params.set("focus", focus);
    return request<GenomeGraph>(`/repositories/${repositoryId}/genome?${params}`);
  },
  getBugs: (repositoryId: string, path: string | null, limit = 60) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (path) params.set("path", path);
    return request<BugHistory>(`/repositories/${repositoryId}/bugs?${params}`);
  },
  getTimeline: (repositoryId: string, bucket: "week" | "month") =>
    request<EvolutionTimeline>(`/repositories/${repositoryId}/timeline?${new URLSearchParams({ bucket })}`),
  getDocs: (repositoryId: string) => request<GeneratedDocuments>(`/repositories/${repositoryId}/docs`),
  rewriteDocs: (repositoryId: string) =>
    request<GeneratedDocuments>(`/repositories/${repositoryId}/docs/rewrite`, { method: "POST" }),
  downloadDoc: async (repositoryId: string, name: string) => {
    const response = await send(`/repositories/${repositoryId}/docs/${encodeURIComponent(name)}/download`);
    saveBlob(await response.blob(), name);
  },
  getAutomation: (repositoryId: string) =>
    request<RepositoryAutomation>(`/repositories/${repositoryId}/automation`),
  putAutomation: (repositoryId: string, autoAnalyze: boolean, prComments?: boolean) =>
    request<RepositoryAutomation>(`/repositories/${repositoryId}/automation`, {
      method: "PUT",
      body: JSON.stringify({ auto_analyze: autoAnalyze, ...(prComments === undefined ? {} : { pr_comments: prComments }) }),
    }),
  pullRequestImpact: (repositoryId: string, number: number) =>
    request<PullRequestImpact>(`/repositories/${repositoryId}/pull-requests/${number}/impact`),

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
  /**
   * Ask a follow-up and receive the answer while Gemini writes it. `onDelta` gets
   * unverified draft text; the resolved turn is the stored, citation-checked answer.
   */
  streamMessage: async (
    conversationId: string,
    content: string,
    language: AnswerLanguage,
    handlers: StreamHandlers,
  ): Promise<ConversationTurn> => {
    const response = await send(`/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify({ content, language, channel: "text" }),
      signal: handlers.signal,
    });
    if (!response.body) throw new ApiError("Streaming is not supported by this browser.", 0, null);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
        const event = /^event: (.+)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (!event || data === undefined) continue;
        const payload: unknown = JSON.parse(data);
        if (event === "delta") handlers.onDelta((payload as { text: string }).text);
        else if (event === "status") handlers.onStatus?.((payload as { model: string }).model);
        else if (event === "fallback") handlers.onFallback?.();
        else if (event === "done") return payload as ConversationTurn;
        else if (event === "error") {
          const problem = payload as { code: string; detail: string };
          throw new ApiError(problem.detail, 500, problem.code);
        }
      }
    }
    throw new ApiError("The answer stream ended early. Try again.", 0, "STREAM_INCOMPLETE");
  },
  deleteConversation: (conversationId: string) =>
    request<void>(`/conversations/${conversationId}`, { method: "DELETE" }),

  askAssistant: (question: string, language: AnswerLanguage) =>
    request<AssistantAnswer>("/assistant/answers", {
      method: "POST",
      body: JSON.stringify({ question, language }),
    }),
  createAssistantSession: (voice: VoiceName, language: AnswerLanguage) =>
    request<VoiceSession>("/voice/assistant-sessions", {
      method: "POST",
      body: JSON.stringify({ voice, language }),
    }),
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
          include_ci: true,
          include_deployments: true,
        },
      }),
    }),
  getProviderSignal: (signalId: string) =>
    request<ProviderSignal>(`/delivery-reports/signals/${encodeURIComponent(signalId)}`),
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
      `/workspaces/${encodeURIComponent(workspaceId)}/audit-events?${new URLSearchParams({ format: "json", limit: String(limit) })}`,
    ),
  downloadAuditCsv: async () => {
    const response = await send(
      `/workspaces/${encodeURIComponent(workspaceId)}/audit-events?${new URLSearchParams({ format: "csv" })}`,
    );
    saveBlob(await response.blob(), `audit-${workspaceId}.csv`);
  },
};

export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error && caught.message ? caught.message : fallback;
}
