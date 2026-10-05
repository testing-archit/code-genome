"use client";

import { Fragment } from "react";

import { parseLink, tokenize } from "../lib/markdown-inline";

/**
 * A deliberately small renderer for the Markdown this app generates (headings, lists,
 * tables, quotes, code spans, bold, links). Text is rendered as React text, never as HTML, so
 * repository content cannot inject markup. Evidence references such as `evidence:ev_…` and
 * `commit:<sha>` become buttons that open the evidence drawer.
 */
function Inline({ text, onEvidence }: { text: string; onEvidence?: (id: string) => void }) {
  const parts = tokenize(text);
  return (
    <>
      {parts.map((part, index) => {
        if (!part) return null;
        if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
        if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
        const link = parseLink(part);
        if (link) {
          return link.href ? (
            <a href={link.href} key={index} rel="noopener noreferrer" target="_blank">{link.text}</a>
          ) : (
            <Fragment key={index}>{link.text}</Fragment>
          );
        }
        if (/^(evidence|commit|module|hotspot):/.test(part)) {
          const id = part.replace(/[.,;)]+$/, "");
          const trailing = part.slice(id.length);
          const label = id.startsWith("commit:") ? `commit ${id.slice(7, 15)}` : id.startsWith("evidence:") ? "source" : id.split(":")[0];
          return onEvidence ? (
            <Fragment key={index}>
              <button className="md-cite" onClick={() => onEvidence(id)} title={`Inspect ${id}`} type="button">{label}</button>
              {trailing}
            </Fragment>
          ) : (
            <code key={index}>{part}</code>
          );
        }
        return <Fragment key={index}>{part}</Fragment>;
      })}
    </>
  );
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "list"; items: Array<{ depth: number; text: string }> }
  | { kind: "table"; header: string[]; rows: string[][] };

function cells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function parse(markdown: string): Block[] {
  const blocks: Block[] = [];
  const lines = markdown.split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!line.trim()) {
      index += 1;
    } else if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
    } else if (line.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].startsWith(">")) quote.push(lines[index++].replace(/^>\s?/, ""));
      blocks.push({ kind: "quote", text: quote.join(" ") });
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: Array<{ depth: number; text: string }> = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        const match = /^(\s*)[-*]\s+(.*)$/.exec(lines[index++])!;
        items.push({ depth: Math.floor(match[1].length / 2), text: match[2] });
      }
      blocks.push({ kind: "list", items });
    } else if (line.trim().startsWith("|") && lines[index + 1]?.trim().match(/^\|?[\s|:-]+\|?$/)) {
      const header = cells(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) rows.push(cells(lines[index++]));
      blocks.push({ kind: "table", header, rows });
    } else {
      const paragraph: string[] = [];
      while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|>|\s*[-*]\s|\|)/.test(lines[index])) paragraph.push(lines[index++]);
      if (paragraph.length === 0) paragraph.push(lines[index++]);
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    }
  }
  return blocks;
}

export function Markdown({ source, onEvidence }: { source: string; onEvidence?: (id: string) => void }) {
  return (
    <div className="markdown">
      {parse(source).map((block, index) => {
        switch (block.kind) {
          case "heading": {
            const Tag = (`h${Math.min(4, block.level + 1)}`) as "h2" | "h3" | "h4";
            return <Tag key={index}><Inline onEvidence={onEvidence} text={block.text} /></Tag>;
          }
          case "quote":
            return <blockquote key={index}><Inline onEvidence={onEvidence} text={block.text} /></blockquote>;
          case "list":
            return (
              <ul key={index}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex} style={item.depth ? { marginLeft: item.depth * 18 } : undefined}>
                    <Inline onEvidence={onEvidence} text={item.text} />
                  </li>
                ))}
              </ul>
            );
          case "table":
            return (
              <div className="table-wrap" key={index}>
                <table className="table">
                  <thead><tr>{block.header.map((cell, cellIndex) => <th key={cellIndex} scope="col"><Inline text={cell} /></th>)}</tr></thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}><Inline onEvidence={onEvidence} text={cell} /></td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default:
            return <p key={index}><Inline onEvidence={onEvidence} text={block.text} /></p>;
        }
      })}
    </div>
  );
}
