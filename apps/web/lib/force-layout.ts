/* A compact, deterministic force layout: repulsion, spring edges, and gentle centering.
   Positions depend only on input order, so the same graph always lays out the same way. */

export type Positioned = { id: string; x: number; y: number };

export function forceLayout(ids: string[], edges: Array<{ source: string; target: string }>, spacing = 46): Map<string, Positioned> {
  const nodes = ids.map((id, index) => {
    const angle = index * 2.399963;
    const radius = 12 * Math.sqrt(index + 1);
    return { id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0 };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = edges
    .map((edge) => [byId.get(edge.source), byId.get(edge.target)] as const)
    .filter((pair): pair is readonly [(typeof nodes)[number], (typeof nodes)[number]] => Boolean(pair[0] && pair[1] && pair[0] !== pair[1]));
  const iterations = nodes.length > 250 ? 180 : 260;
  for (let step = 0; step < iterations; step++) {
    const alpha = 1 - step / iterations;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distance = dx * dx + dy * dy;
        if (distance === 0) {
          dx = 0.1;
          dy = 0.1;
          distance = 0.02;
        }
        if (distance > 90000) continue;
        const force = (900 * alpha) / distance;
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      }
    }
    for (const [source, target] of links) {
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = ((distance - spacing) / distance) * 0.06 * alpha;
      source.vx += dx * force;
      source.vy += dy * force;
      target.vx -= dx * force;
      target.vy -= dy * force;
    }
    for (const node of nodes) {
      node.vx -= node.x * 0.004 * alpha;
      node.vy -= node.y * 0.004 * alpha;
      node.x += Math.max(-30, Math.min(30, node.vx));
      node.y += Math.max(-30, Math.min(30, node.vy));
      node.vx *= 0.55;
      node.vy *= 0.55;
    }
  }
  return new Map(nodes.map((node) => [node.id, { id: node.id, x: node.x, y: node.y }]));
}
