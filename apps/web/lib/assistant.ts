import type { Repository } from "@code-genome/contracts";

/** Match a spoken repository name ("ky", "sindresorhus/ky", "code genome") to the workspace. */
export function resolveRepository(name: string, repositories: Repository[]): Repository | null {
  const wanted = name.trim().toLowerCase().replace(/\s+/g, "-");
  if (!wanted) return repositories.length === 1 ? repositories[0] : null;
  const exact = repositories.find((item) => item.external_id.toLowerCase() === wanted || item.external_id.split("/").pop()!.toLowerCase() === wanted);
  if (exact) return exact;
  const partial = repositories.filter((item) => item.external_id.toLowerCase().includes(wanted) || wanted.includes(item.external_id.split("/").pop()!.toLowerCase()));
  return partial.length === 1 ? partial[0] : null;
}
