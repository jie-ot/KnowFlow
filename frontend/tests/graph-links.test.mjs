import assert from "node:assert/strict";
import test from "node:test";
import { connectSuggestions } from "../src/lib/graph-links.ts";

test("bulk recommendations retain failures, report actual outcomes, and retry only remaining items", async () => {
  const items = [{ source: "a", target: "b" }, { source: "b", target: "c" }, { source: "c", target: "d" }];
  const attempted = [];
  const result = await connectSuggestions(items, async (item) => {
    attempted.push(item);
    if (item === items[1]) return false;
    if (item === items[2]) throw new Error("network unavailable");
    return true;
  });
  assert.deepEqual(attempted, items);
  assert.deepEqual(result.successes, [items[0]]);
  assert.deepEqual(result.failures, [items[1], items[2]]);
  const retry = await connectSuggestions(result.failures, async () => true);
  assert.deepEqual(retry.successes, [items[1], items[2]]);
  assert.deepEqual(retry.failures, []);
});

test("one failed recommendation remains available without affecting a subsequent success", async () => {
  const item = { source: "a", target: "b" };
  const failed = await connectSuggestions([item], async () => false);
  assert.deepEqual(failed.failures, [item]);
  const retried = await connectSuggestions(failed.failures, async () => true);
  assert.deepEqual(retried.successes, [item]);
});
