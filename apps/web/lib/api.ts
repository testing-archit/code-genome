import type {
  AnalysisRun,
  Evidence,
  GraphProjection,
  ProblemDetail,
  Repository,
  RepositoryInventory,
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
