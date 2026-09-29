"use client";

/* Small, dependency-free charts for model evaluation. Series colours come from the
   validated --chart-* tokens; values are always printed as text next to their marks. */

export type Series = { key: string; label: string; color: string };

export const baselineSeries: Series = { key: "baseline", label: "Baseline", color: "var(--chart-baseline)" };
export const modelSeries: Series = { key: "model", label: "Model", color: "var(--chart-model)" };
export const challengerSeries: Series = { key: "challenger", label: "Challenger", color: "var(--chart-challenger)" };

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {series.map((item) => (
        <span key={item.key} style={{ "--swatch": item.color } as React.CSSProperties}><i />{item.label}</span>
      ))}
    </div>
  );
}

/** Grouped horizontal bars on a shared 0–1 scale (metrics such as ROC-AUC or recall). */
export function MetricBars({
  groups,
  series,
  caption,
}: {
  groups: Array<{ label: string; values: Record<string, number | undefined> }>;
  series: Series[];
  caption: string;
}) {
  return (
    <figure style={{ margin: 0, display: "grid", gap: 12 }}>
      <Legend series={series} />
      <div className="bars">
        {groups.map((group) => (
          <div className="bar-group" key={group.label}>
            <span>{group.label}</span>
            {series.map((item) => {
              const value = group.values[item.key];
              if (value === undefined) return null;
              return (
                <div className="bar-row" key={item.key} title={`${item.label}: ${value.toFixed(3)}`}>
                  <div className="bar-track">
                    <i style={{ width: `${Math.max(0.5, Math.min(1, value) * 100)}%`, "--swatch": item.color } as React.CSSProperties} />
                  </div>
                  <b>{value.toFixed(2)}</b>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="bar-axis" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => <span key={tick} style={{ left: `${tick * 100}%` }}>{tick}</span>)}
      </div>
      <figcaption className="muted small">{caption}</figcaption>
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead><tr><th>Metric</th>{series.map((item) => <th key={item.key}>{item.label}</th>)}</tr></thead>
        <tbody>{groups.map((group) => <tr key={group.label}><th>{group.label}</th>{series.map((item) => <td key={item.key}>{group.values[item.key]?.toFixed(3) ?? "n/a"}</td>)}</tr>)}</tbody>
      </table>
    </figure>
  );
}

/** Single-series ranked bars with a free scale (feature importance and similar). */
export function RankedBars({ items, format = (value) => value.toFixed(3) }: { items: Array<{ label: string; value: number }>; format?: (value: number) => string }) {
  const peak = Math.max(1e-9, ...items.map((item) => Math.abs(item.value)));
  return (
    <div className="bars" style={{ gap: 8 }}>
      {items.map((item) => (
        <div className="diverging-row" key={item.label} style={{ gridTemplateColumns: "170px minmax(0, 1fr) 52px" }} title={`${item.label}: ${format(item.value)}`}>
          <span className="truncate">{item.label}</span>
          <div className="bar-track"><i style={{ width: `${Math.max(0.5, (Math.max(0, item.value) / peak) * 100)}%`, "--swatch": "var(--chart-model)" } as React.CSSProperties} /></div>
          <b className="small" style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{format(item.value)}</b>
        </div>
      ))}
    </div>
  );
}

/** Signed bars around a zero line: positive pushes towards the predicted class. */
export function DivergingBars({ items }: { items: Array<{ label: string; value: number }> }) {
  const peak = Math.max(1e-9, ...items.map((item) => Math.abs(item.value)));
  return (
    <div className="bars" style={{ gap: 8 }}>
      <div className="legend" aria-hidden="true">
        <span style={{ "--swatch": "var(--chart-model)" } as React.CSSProperties}><i />Raises the prediction</span>
        <span style={{ "--swatch": "var(--chart-challenger)" } as React.CSSProperties}><i />Lowers the prediction</span>
      </div>
      {items.map((item) => {
        const width = (Math.abs(item.value) / peak) * 50;
        const positive = item.value >= 0;
        return (
          <div className="diverging-row" key={item.label} title={`${item.label}: ${item.value.toFixed(3)}`}>
            <span className="truncate">{item.label}</span>
            <div className="diverging-track">
              <i
                style={{
                  left: positive ? "50%" : `${50 - width}%`,
                  width: `${Math.max(0.4, width)}%`,
                  borderRadius: positive ? "0 4px 4px 0" : "4px 0 0 4px",
                  "--swatch": positive ? "var(--chart-model)" : "var(--chart-challenger)",
                } as React.CSSProperties}
              />
            </div>
            <b className="small" style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{positive ? "+" : ""}{item.value.toFixed(2)}</b>
          </div>
        );
      })}
    </div>
  );
}

/** Reliability diagram: predicted probability vs observed frequency, with the ideal diagonal. */
export function CalibrationPlot({ predicted, observed }: { predicted: number[]; observed: number[] }) {
  const size = 220;
  const pad = 28;
  const scale = (value: number) => pad + value * (size - pad * 1.5);
  const y = (value: number) => size - pad - value * (size - pad * 1.5);
  const points = predicted.map((value, index) => `${scale(value)},${y(observed[index] ?? 0)}`).join(" ");
  return (
    <figure style={{ margin: 0 }}>
      <svg aria-label="Calibration: predicted probability against observed frequency" className="chart-svg" role="img" style={{ maxWidth: 300 }} viewBox={`0 0 ${size} ${size}`}>
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line className="grid" x1={scale(0)} x2={scale(1)} y1={y(tick)} y2={y(tick)} />
            <text className="axis-label" textAnchor="end" x={pad - 6} y={y(tick) + 4}>{tick}</text>
            <text className="axis-label" textAnchor="middle" x={scale(tick)} y={size - pad + 16}>{tick}</text>
          </g>
        ))}
        <line stroke="var(--rule-strong)" strokeDasharray="4 4" strokeWidth="1.5" x1={scale(0)} x2={scale(1)} y1={y(0)} y2={y(1)} />
        <polyline fill="none" points={points} stroke="var(--chart-model)" strokeWidth="2" />
        {predicted.map((value, index) => (
          <circle cx={scale(value)} cy={y(observed[index] ?? 0)} fill="var(--chart-model)" key={index} r="4.5" stroke="var(--glass)" strokeWidth="2">
            <title>{`Predicted ${value.toFixed(2)}, observed ${(observed[index] ?? 0).toFixed(2)}`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="muted small">Points on the dashed line are perfectly calibrated. Horizontal: predicted probability. Vertical: share that actually needed a fix.</figcaption>
    </figure>
  );
}

/** Confusion matrix as a single-hue heatmap, normalised per true class (row). */
export function ConfusionMatrix({ labels, matrix }: { labels: string[]; matrix: number[][] }) {
  return (
    <figure style={{ margin: 0, display: "grid", gap: 8 }}>
      <div className="heatmap" role="table" aria-label="Confusion matrix, rows are true intents and columns are predicted" style={{ gridTemplateColumns: `90px repeat(${labels.length}, minmax(34px, 1fr))` }}>
        <div className="head" role="columnheader">true ↓ / pred →</div>
        {labels.map((label) => <div className="head" key={label} role="columnheader">{label}</div>)}
        {matrix.map((row, rowIndex) => {
          const total = Math.max(1, row.reduce((sum, value) => sum + value, 0));
          return [
            <div className="head" key={`h-${labels[rowIndex]}`} role="rowheader" style={{ justifyItems: "start", placeItems: "center start" }}>{labels[rowIndex]}</div>,
            ...row.map((value, columnIndex) => (
              <div
                className="cell"
                data-strong={value / total > 0.55}
                key={`${rowIndex}-${columnIndex}`}
                role="cell"
                style={{ "--v": String(value / total) } as React.CSSProperties}
                title={`${labels[rowIndex]} predicted as ${labels[columnIndex]}: ${value}`}
              >
                {value || ""}
              </div>
            )),
          ];
        })}
      </div>
      <figcaption className="muted small">Counts from cross-validation. Shade shows the share of each true intent; the diagonal is correct.</figcaption>
    </figure>
  );
}
