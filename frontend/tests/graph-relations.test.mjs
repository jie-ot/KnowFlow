import assert from "node:assert/strict";
import test from "node:test";
import { directedLine, relationDescriptions, relationLabels } from "../src/lib/graph-relations.ts";

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const near = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} should be near ${expected}`);
function assertDirectedLine(line, source, target) {
  assert.ok(line);
  for (const point of [line.start, line.midpoint, line.end])
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
  assert.doesNotMatch(line.d, /NaN|Infinity|Q|C/);
  assert.equal(line.d, `M ${line.start.x} ${line.start.y} L ${line.midpoint.x} ${line.midpoint.y} L ${line.end.x} ${line.end.y}`);
  near(distance(line.start, source), source.r + 2);
  near(distance(line.end, target), target.r + 5);
  near(line.midpoint.x, (line.start.x + line.end.x) / 2);
  near(line.midpoint.y, (line.start.y + line.end.y) / 2);
  const dx = target.x - source.x, dy = target.y - source.y;
  for (const point of [line.start, line.midpoint, line.end])
    near((point.x - source.x) * dy - (point.y - source.y) * dx, 0, 1e-7);
  const incoming = { x: line.midpoint.x - line.start.x, y: line.midpoint.y - line.start.y };
  const outgoing = { x: line.end.x - line.midpoint.x, y: line.end.y - line.midpoint.y };
  near(incoming.x, outgoing.x);
  near(incoming.y, outgoing.y);
  assert.ok(incoming.x * dx + incoming.y * dy > 0, "the midpoint arrow points from source toward target");
  assert.ok(outgoing.x * dx + outgoing.y * dy > 0, "the final segment retains the same direction");
}

test("horizontal line clears each node and has a precise straight midpoint for the open-V marker", () => {
  const source = { x: 0, y: 10, r: 5 }, target = { x: 100, y: 10, r: 10 };
  const line = directedLine(source, target);
  assertDirectedLine(line, source, target);
  assert.deepEqual(line.start, { x: 7, y: 10 });
  assert.deepEqual(line.end, { x: 85, y: 10 });
  assert.deepEqual(line.midpoint, { x: 46, y: 10 });
});

test("vertical and diagonal lines keep all rendered points collinear with exact endpoint clearances", () => {
  for (const [source, target] of [
    [{ x: 5, y: -50, r: 8 }, { x: 5, y: 150, r: 13 }],
    [{ x: 90, y: 130, r: 8 }, { x: -30, y: -75, r: 16 }],
    [{ x: -500, y: 200, r: 180 }, { x: 400, y: -300, r: 260 }],
    [{ x: 0, y: 0, r: 1 }, { x: 9.001, y: 0, r: 1 }],
    [{ x: 0, y: 0, r: 0 }, { x: 7.001, y: 0, r: 0 }],
  ]) assertDirectedLine(directedLine(source, target), source, target);
});

test("reversing a relation reverses both straight segments and the midpoint marker direction", () => {
  const source = { x: -35, y: 12, r: 8 }, target = { x: 220, y: 165, r: 17 };
  const forward = directedLine(source, target), reverse = directedLine(target, source);
  assertDirectedLine(forward, source, target);
  assertDirectedLine(reverse, target, source);
  const forwardDirection = { x: forward.midpoint.x - forward.start.x, y: forward.midpoint.y - forward.start.y };
  const reverseDirection = { x: reverse.midpoint.x - reverse.start.x, y: reverse.midpoint.y - reverse.start.y };
  assert.ok(forwardDirection.x * reverseDirection.x + forwardDirection.y * reverseDirection.y < 0);
  near(distance(reverse.start, target), target.r + 2);
  near(distance(reverse.end, source), source.r + 5);
});

test("overlap, insufficient clearance and invalid coordinates return null without NaN paths", () => {
  const source = { x: 20, y: 30, r: 5 };
  for (const target of [source, { ...source, x: 21 }, { ...source, x: 37 },
    { ...source, x: NaN }, { ...source, y: Infinity }, { ...source, r: -1 }, { ...source, r: NaN }])
    assert.equal(directedLine(source, target), null);
  assert.equal(directedLine({ ...source, x: NaN }, { x: 200, y: 30, r: 5 }), null);
  assert.equal(directedLine({ ...source, r: -1 }, { x: 200, y: 30, r: 5 }), null);
  assert.equal(directedLine({ x: -Number.MAX_VALUE, y: 0, r: 0 }, { x: Number.MAX_VALUE, y: 0, r: 0 }), null);
});

test("all four relation descriptions retain explicit source and target meaning", () => {
  assert.deepEqual(Object.keys(relationLabels), ["related", "prerequisite", "contains", "applies"]);
  for (const description of Object.values(relationDescriptions)) assert.ok(description.includes("A") && description.includes("B"));
  assert.match(relationDescriptions.prerequisite, /先学 A，再学 B/);
  assert.match(relationDescriptions.contains, /A 包含 B/);
  assert.match(relationDescriptions.applies, /A 应用于 B/);
});
