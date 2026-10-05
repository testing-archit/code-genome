/** API timestamps are UTC; SQLite returns them without an offset, so treat bare values as UTC. */
export function parseTime(value: string): Date {
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(parseTime(value));
}

/** A moment in time, shown as a calendar date in the viewer's time zone. */
export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(parseTime(value));
}

/**
 * A date-only value (such as a report scope boundary stored as `…T00:00:00Z` or
 * `…T23:59:59Z`), shown as the UTC calendar date it names. Formatting it in local time
 * would move `23:59:59Z` to the next day east of UTC.
 */
export function formatScopeDate(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeZone: "UTC" }).format(parseTime(value));
}

/** `YYYY-MM-DD` for the viewer's local calendar day, `daysAgo` days before today (for date inputs). */
export function localDateInput(daysAgo = 0, now: Date = new Date()): string {
  const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo);
  return localDayKey(date);
}

/** `YYYY-MM-DD` for the local calendar day containing `date`. */
export function localDayKey(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "never";
  const seconds = (parseTime(value).getTime() - Date.now()) / 1000;
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function shortSha(sha: string | null | undefined, length = 10): string {
  return sha ? sha.slice(0, length) : "—";
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}`;
}

export function nodeTitle(naturalKey: string): string {
  const symbol = naturalKey.split("#")[1];
  if (symbol) {
    const [, name] = symbol.split(":");
    if (name) return name;
  }
  const pieces = naturalKey.split(/[/:#]/).filter(Boolean);
  return pieces.at(-1) ?? naturalKey;
}

export function repoName(externalId: string): { owner: string; name: string } {
  const [owner = "", name = externalId] = externalId.split("/");
  return { owner, name };
}

/* Module bands use a stain-like hue ramp so adjacent modules stay distinguishable.
   Rose and red hues are left out: rose is reserved for hotspots. */
const bandHues = [258, 190, 38, 150, 285, 215, 95, 172, 60, 238];
export function moduleColor(index: number): string {
  if (index < 0) return "var(--rule-strong)";
  const hue = bandHues[index % bandHues.length];
  const lightness = index >= bandHues.length ? 62 : 52;
  return `hsl(${hue} 52% ${lightness}%)`;
}

export const REFUSAL_PREFIXES = ["Insufficient repository evidence", "No supporting evidence was identified"];
export function isRefusal(answer: string, evidenceIds: string[]): boolean {
  return evidenceIds.length === 0 || REFUSAL_PREFIXES.some((prefix) => answer.startsWith(prefix));
}
