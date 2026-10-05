import assert from "node:assert/strict";
import { test } from "node:test";

import { forceLayout } from "../../lib/force-layout.ts";

test("layout is deterministic and keeps linked nodes closer than unlinked ones", () => {
  const ids = ["a", "b", "c", "d"];
  const edges = [{ source: "a", target: "b" }, { source: "c", target: "d" }];
  const first = forceLayout(ids, edges);
  const second = forceLayout(ids, edges);
  assert.deepEqual([...first.values()], [...second.values()]);
  const distance = (left: string, right: string) => {
    const p = first.get(left)!;
    const q = first.get(right)!;
    return Math.hypot(p.x - q.x, p.y - q.y);
  };
  assert.ok(distance("a", "b") < distance("a", "c"));
  for (const point of first.values()) assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
});

test("edges to unknown nodes and self-loops are ignored", () => {
  const layout = forceLayout(["a"], [{ source: "a", target: "a" }, { source: "a", target: "missing" }]);
  assert.equal(layout.size, 1);
});
