"use client";

import type { Architecture, RepositoryInventory } from "@code-genome/contracts";
import { useMemo, useState } from "react";

import { formatBytes, moduleColor } from "../lib/format";

export type Band = {
  path: string;
  moduleIndex: number;
  moduleName: string | null;
  weight: number;
  hotspot: number;
  size: number;
};

/**
 * One band per source file, ordered by path so directories cluster like chromosome
 * regions. Colour marks the inferred module; opacity marks relative hotspot score.
 */
export function buildBands(architecture: Architecture | null, inventory: RepositoryInventory | null, limit = 360): Band[] {
  const moduleByPath = new Map<string, number>();
  architecture?.modules.forEach((module, index) => module.file_paths.forEach((path) => moduleByPath.set(path, index)));
  const hotspotByPath = new Map(architecture?.hotspots.map((item) => [item.path, item.score]) ?? []);
  const files = inventory?.files.filter((file) => file.analyzed) ?? [];
  const source = files.length
    ? files.map((file) => ({ path: file.path, size: file.size }))
    : [...moduleByPath.keys()].map((path) => ({ path, size: 0 }));
  source.sort((a, b) => a.path.localeCompare(b.path));
  const step = Math.max(1, Math.ceil(source.length / limit));
  const bands: Band[] = [];
  for (let index = 0; index < source.length; index += step) {
    const file = source[index];
    const moduleIndex = moduleByPath.get(file.path) ?? -1;
    bands.push({
      path: file.path,
      moduleIndex,
      moduleName: moduleIndex >= 0 ? architecture?.modules[moduleIndex]?.name ?? null : null,
      weight: 1,
      hotspot: hotspotByPath.get(file.path) ?? 0,
      size: file.size,
    });
  }
  return bands;
}

export function GenomeStrip({
  bands,
  architecture,
  onSelect,
  selectedPath,
}: {
  bands: Band[];
  architecture: Architecture | null;
  onSelect?: (path: string) => void;
  selectedPath?: string | null;
}) {
  const [hovered, setHovered] = useState<Band | null>(null);
  const shown = hovered ?? bands.find((band) => band.path === selectedPath) ?? null;
  const modules = useMemo(() => architecture?.modules.slice(0, 10) ?? [], [architecture]);

  if (bands.length === 0) {
    return <div className="genome-strip" aria-label="No analyzed files" />;
  }

  return (
    <div>
      <div className="genome-strip" role="group" aria-label={`Genome of ${bands.length} analyzed files`} onMouseLeave={() => setHovered(null)}>
        {bands.map((band, index) => (
          <button
            aria-label={`${band.path}${band.moduleName ? `, module ${band.moduleName}` : ""}${band.hotspot ? `, hotspot ${Math.round(band.hotspot * 100)}` : ""}`}
            aria-pressed={selectedPath === band.path}
            className="genome-band"
            key={band.path}
            onClick={() => onSelect?.(band.path)}
            onFocus={() => setHovered(band)}
            onMouseEnter={() => setHovered(band)}
            style={{
              "--band-color": band.hotspot > 0.45 ? "var(--eosin)" : moduleColor(band.moduleIndex),
              "--band-opacity": String(0.45 + Math.min(0.55, band.hotspot * 0.8 + (band.moduleIndex >= 0 ? 0.25 : 0))),
              "--band-delay": `${Math.min(600, index * 2)}ms`,
            } as React.CSSProperties}
            type="button"
          />
        ))}
      </div>
      <div className="genome-readout" aria-live="polite">
        {shown ? (
          <>
            <code className="truncate">{shown.path}</code>
            <span className="muted">{shown.moduleName ? `module ${shown.moduleName}` : "no module inferred"}</span>
            {shown.hotspot > 0 && <span className="badge badge-eosin">hotspot {Math.round(shown.hotspot * 100)}</span>}
            {shown.size > 0 && <span className="muted">{formatBytes(shown.size)}</span>}
          </>
        ) : (
          <span className="muted">Point at a band to read a file. Rose bands are change hotspots.</span>
        )}
      </div>
      {modules.length > 0 && (
        <div className="genome-legend">
          {modules.map((module, index) => (
            <span key={module.id} style={{ "--band-color": moduleColor(index) } as React.CSSProperties}>
              <i />{module.name}
            </span>
          ))}
          <span style={{ "--band-color": "var(--eosin)" } as React.CSSProperties}><i />Hotspot</span>
        </div>
      )}
    </div>
  );
}
