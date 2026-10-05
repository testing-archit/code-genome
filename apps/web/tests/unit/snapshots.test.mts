import assert from "node:assert/strict";
import { test } from "node:test";

import { orderSnapshots } from "../../lib/snapshots.ts";

test("branch-head snapshots come before past points, latest point first", () => {
  const ordered = orderSnapshots([
    { id: "past-july", as_of: "2026-07-15" },
    { id: "head", as_of: null },
    { id: "past-sept", as_of: "2026-09-01" },
  ]);
  assert.deepEqual(ordered.map((item) => item.id), ["head", "past-sept", "past-july"]);
});
