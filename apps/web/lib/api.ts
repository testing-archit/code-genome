import type {
  AnalysisRun,
  Architecture,
  DeliveryReport,
  Evidence,
  GroundedAnswer,
  GraphProjection,
  ImpactAnalysis,
  ProblemDetail,
  Repository,
  RepositoryInventory,
  RiskAnalysis,
} from "@code-genome/contracts";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const workspaceId = process.env.NEXT_PUBLIC_WORKSPACE_ID ?? "ws_demo";
const userId = process.env.NEXT_PUBLIC_USER_ID ?? "usr_demo";

function requestHeaders(idempotencyKey?: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Workspace-ID": workspaceId,
    "X-User-ID": userId,
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { ...requestHeaders(), ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = (await response.json()) as ProblemDetail;
    throw new Error(problem.detail || "The API request failed.");
  }
  return (await response.json()) as T;
}

export function listRepositories(): Promise<Repository[]> {
  return request<Repository[]>("/repositories");
}

export function createRepository(cloneUrl: string, branch: string): Promise<Repository> {
  return request<Repository>("/repositories", {
    method: "POST",
    headers: requestHeaders(crypto.randomUUID()),
    body: JSON.stringify({ clone_url: cloneUrl, default_branch: branch }),
  });
}

export function listAnalyses(repositoryId: string): Promise<AnalysisRun[]> {
  return request<AnalysisRun[]>(`/repositories/${repositoryId}/analyses`);
}

export function createAnalysis(repositoryId: string, branch: string): Promise<AnalysisRun> {
  return request<AnalysisRun>(`/repositories/${repositoryId}/analyses`, {
    method: "POST",
    headers: requestHeaders(crypto.randomUUID()),
    body: JSON.stringify({ refs: [branch] }),
  });
}

export function getAnalysis(runId: string): Promise<AnalysisRun> {
  return request<AnalysisRun>(`/analyses/${runId}`);
}

export function getGraph(repositoryId: string, snapshotSha: string): Promise<GraphProjection> {
  const query = new URLSearchParams({ snapshot_sha: snapshotSha, limit: "1000" });
  return request<GraphProjection>(`/repositories/${repositoryId}/graph?${query}`);
}

export function getEvidence(evidenceId: string): Promise<Evidence> {
  return request<Evidence>(`/evidence/${evidenceId}`);
}

export function getRepositoryInventory(repositoryId: string): Promise<RepositoryInventory> {
  return request<RepositoryInventory>(
    `/repositories/${repositoryId}/inventory?commit_limit=100&file_limit=2000`,
  );
}

export function getArchitecture(repositoryId: string): Promise<Architecture> {
  return request<Architecture>(`/repositories/${repositoryId}/architecture?relationship_limit=100`);
}

export function createDeliveryReport(
  repositoryId: string,
  text: string,
  from: string,
  to: string,
  branch: string,
): Promise<DeliveryReport> {
  return request<DeliveryReport>("/delivery-reports", {
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
  });
}

export function assessDeliveryReport(reportId: string): Promise<DeliveryReport> {
  return request<DeliveryReport>(`/delivery-reports/${reportId}/assessments`, {
    method: "POST",
  });
}

export async function downloadDeliveryReport(reportId: string): Promise<void> {
  const response = await fetch(`${apiUrl}/delivery-reports/${reportId}/download`, {
    headers: requestHeaders(),
  });
  if (!response.ok) throw new Error("The delivery audit could not be downloaded.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `delivery-audit-${reportId}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

export function getRisk(repositoryId: string): Promise<RiskAnalysis> {
  return request<RiskAnalysis>(`/repositories/${repositoryId}/risk`);
}

export function getImpact(repositoryId: string, path: string): Promise<ImpactAnalysis> {
  const query = new URLSearchParams({ path });
  return request<ImpactAnalysis>(`/repositories/${repositoryId}/impact?${query}`);
}

export function askRepository(repositoryId: string, question: string): Promise<GroundedAnswer> {
  return request<GroundedAnswer>("/chat/answers", {
    method: "POST",
    body: JSON.stringify({ repository_id: repositoryId, question }),
  });
}

export function rateAnswer(answerId: string, rating: -1 | 1): Promise<void> {
  return request(`/chat/answers/${answerId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating }),
  });
}
