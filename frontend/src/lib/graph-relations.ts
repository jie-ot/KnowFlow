export const relationLabels = { related: "相关", prerequisite: "前置知识", contains: "包含", applies: "应用" } as const;
export type GraphRelationKind = keyof typeof relationLabels;
export const relationDescriptions = {
  related: "A 关联 B",
  prerequisite: "A 是 B 的前置知识（先学 A，再学 B）",
  contains: "A 包含 B",
  applies: "A 应用于 B",
} as const;

type NodeCircle = { x: number; y: number; r: number };
type Point = { x: number; y: number };
export type DirectedLine = { d: string; start: Point; midpoint: Point; end: Point };

/** Trim a straight connection against each node's clearance circle. */
export function directedLine(source: NodeCircle, target: NodeCircle): DirectedLine | null {
  if (![source.x, source.y, source.r, target.x, target.y, target.r].every(Number.isFinite) ||
    source.r < 0 || target.r < 0) return null;
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.hypot(dx, dy);
  const sourceClearance = source.r + 2, targetClearance = target.r + 5;
  if (!Number.isFinite(distance) || distance <= sourceClearance + targetClearance) return null;
  const ux = dx / distance, uy = dy / distance;
  const start = { x: source.x + ux * sourceClearance, y: source.y + uy * sourceClearance };
  const end = { x: target.x - ux * targetClearance, y: target.y - uy * targetClearance };
  const midpoint = { x: start.x / 2 + end.x / 2, y: start.y / 2 + end.y / 2 };
  if (![start.x, start.y, midpoint.x, midpoint.y, end.x, end.y].every(Number.isFinite)) return null;
  // An explicit middle vertex places SVG marker-mid halfway along the line.
  return { d: `M ${start.x} ${start.y} L ${midpoint.x} ${midpoint.y} L ${end.x} ${end.y}`,
    start, midpoint, end };
}
