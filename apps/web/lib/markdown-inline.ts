/* Inline Markdown tokens for the app's small renderer (components/markdown.tsx). */

export const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]\n]+\]\([^)\s]+\)|\b(?:evidence|commit|module|hotspot):[\w./@-]{6,})/g;
const LINK = /^\[([^\]\n]+)\]\(([^)\s]+)\)$/;

/** Split a line into plain text and inline tokens (code, bold, links, evidence references). */
export function tokenize(text: string): string[] {
  return text.split(INLINE).filter(Boolean);
}

/**
 * A Markdown link token as `{ text, href }`. `href` is null unless the URL is absolute http(s);
 * README-relative anchors and other schemes (javascript:, data:) render as plain text.
 */
export function parseLink(token: string): { text: string; href: string | null } | null {
  const match = LINK.exec(token);
  if (!match) return null;
  return { text: match[1], href: /^https?:\/\//i.test(match[2]) ? match[2] : null };
}
