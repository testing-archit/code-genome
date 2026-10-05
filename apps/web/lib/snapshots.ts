import type { SnapshotSummary } from "@code-genome/contracts";

/** Branch-head snapshots first (newest analysis first), then past points (latest point first). */
export function orderSnapshots<T extends Pick<SnapshotSummary, "as_of">>(items: T[]): T[] {
  const heads = items.filter((item) => !item.as_of);
  const past = items.filter((item) => item.as_of).sort((a, b) => (b.as_of ?? "").localeCompare(a.as_of ?? ""));
  return [...heads, ...past];
}
