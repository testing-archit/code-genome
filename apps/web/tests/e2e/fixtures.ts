import type { Page, Route } from "@playwright/test";

export const REPO = "repo_e2e";
const SHA = "a".repeat(40);
const FIX = "b".repeat(40);
const INTRO = "c".repeat(40);

const repository = {
  id: REPO,
  provider: "github",
  external_id: "acme/widget",
  clone_url: "https://github.com/acme/widget.git",
  default_branch: "main",
  status: "REGISTERED",
  created_at: "2026-09-01T00:00:00Z",
};

const run = {
  id: "run_e2e",
  repository_id: REPO,
  snapshot_sha: SHA,
  requested_refs: ["main"],
  version: "structural-genome@0.2.0",
  state: "SUCCEEDED",
  progress: 1,
  diagnostics: ["Published 3 nodes."],
  created_at: "2026-09-02T00:00:00Z",
  started_at: "2026-09-02T00:00:00Z",
  completed_at: "2026-09-02T00:01:00Z",
  error_code: null,
  error_detail: null,
  stage: "complete",
  progress_counts: { files_indexed: 3 },
  as_of: null,
};

const genome = {
  scope: { repository_id: REPO, snapshot_id: "snap_e2e", snapshot_sha: SHA, analysis_version: "structural-genome@0.2.0", sources: ["structural graph"] },
  version: "genome-graph@1",
  focus: null,
  focus_node_id: null,
  nodes: [
    { id: "component:src", kind: "component", label: "src", properties: { files: 2 }, evidence_ids: [], inferred: true },
    { id: "file:src/api.ts", kind: "file", label: "api.ts", properties: { path: "src/api.ts", loc: 40 }, evidence_ids: ["evidence:ev_api"], inferred: false },
    { id: "file:src/db.ts", kind: "file", label: "db.ts", properties: { path: "src/db.ts", loc: 25 }, evidence_ids: ["evidence:ev_db"], inferred: false },
  ],
  edges: [
    { id: "e1", kind: "CALLS", source: "file:src/api.ts", target: "file:src/db.ts", weight: 1, confidence: 0.8, inferred: true, evidence_ids: ["evidence:ev_call"] },
    { id: "e2", kind: "BELONGS_TO_MODULE", source: "file:src/api.ts", target: "component:src", weight: 1, confidence: 1, inferred: true, evidence_ids: [] },
  ],
  node_counts: { component: 1, file: 2 },
  edge_counts: { CALLS: 1, BELONGS_TO_MODULE: 1 },
  total_nodes: 3,
  total_edges: 2,
  truncated: false,
  limitations: ["Inferred relationships are candidates."],
};

const bugs = {
  repository_id: REPO,
  snapshot_sha: SHA,
  analysis_version: "szz-lite@1",
  fix_rule: "keyword subject; merges excluded",
  path: null,
  fixes: [
    {
      fix_sha: FIX,
      subject: "fix retry crash",
      author: "Dev",
      authored_at: "2026-09-01T10:00:00Z",
      files: ["src/api.ts"],
      introducing: [
        {
          introducing_sha: INTRO,
          subject: "add retry",
          author: "Dev",
          authored_at: "2026-08-20T10:00:00Z",
          path: "src/api.ts",
          lines: 2,
          confidence: 0.7,
          evidence_id: "evidence:ev_bug",
          evidence: { fix_sha: FIX, parent_sha: SHA, path: "src/api.ts", fix_removed_ranges: [[3, 4]], blamed_ranges: [[3, 4]], bulk_introducing_commit: false, shallow_boundary: false },
          bulk_commit: false,
          shallow_boundary: false,
        },
      ],
    },
  ],
  files: [{ path: "src/api.ts", fix_commits: 1, introducing_commits: 1, fix_shas: [FIX], introducing_shas: [INTRO], lines: 2 }],
  counts: { fix_commits: 1, bug_links: 1, files: 1 },
  limitations: [],
};

type Overrides = Partial<Record<string, (route: Route) => Promise<void> | void>>;

/**
 * Serve the mocked API for every browser request to `/api/v1/` (whatever NEXT_PUBLIC_API_URL
 * points at). Unknown endpoints answer 404 problem JSON, like the real API.
 */
export async function mockApi(page: Page, overrides: Overrides = {}) {
  await page.route(/\/api\/v1\//, async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    const custom = Object.entries(overrides).find(([prefix]) => path.startsWith(prefix));
    if (custom?.[1]) return custom[1](route);
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/repositories") return json([repository]);
    if (path === `/repositories/${REPO}/analyses`) return json([run]);
    if (path === `/repositories/${REPO}/genome`) return json(genome);
    if (path === `/repositories/${REPO}/bugs`) return json(bugs);
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ type: "about:blank", title: "Not found", status: 404, detail: "Not mocked.", code: "NOT_FOUND" }),
    });
  });
}
