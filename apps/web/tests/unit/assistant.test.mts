import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveRepository } from "../../lib/assistant.ts";

const repo = (id: string, external_id: string) => ({ id, external_id, provider: "github", clone_url: "", default_branch: "main", status: "REGISTERED", created_at: "" }) as const;
const repositories = [repo("r1", "sindresorhus/ky"), repo("r2", "testing-archit/code-genome"), repo("r3", "testing-archit/ecocred")];

test("spoken repository names resolve to one workspace repository", () => {
  assert.equal(resolveRepository("ky", [...repositories])?.id, "r1");
  assert.equal(resolveRepository("sindresorhus/ky", [...repositories])?.id, "r1");
  assert.equal(resolveRepository("Code Genome", [...repositories])?.id, "r2");
  assert.equal(resolveRepository("testing-archit", [...repositories]), null); // ambiguous
  assert.equal(resolveRepository("billing", [...repositories]), null);
  assert.equal(resolveRepository("", [repositories[0]])?.id, "r1"); // only one repository
});
