import assert from "node:assert/strict";
import test from "node:test";
import { LatestRequest } from "../src/lib/search-request.ts";

test("a slow old search cannot replace a newer result even if transport ignores abort", async () => {
  const gate = new LatestRequest();
  let finishOld;
  const old = gate.begin();
  const oldRequest = new Promise((resolve) => { finishOld = resolve; });
  const displayed = [];
  const handlingOld = oldRequest.then((value) => { if (old.current()) displayed.push(value); });
  const latest = gate.begin();
  assert.equal(old.signal.aborted, true);
  assert.equal(latest.current(), true);
  displayed.push("new result");
  finishOld("obsolete result");
  await handlingOld;
  assert.deepEqual(displayed, ["new result"]);
});

test("closing search cancels its request and reopening starts a new generation", () => {
  const gate = new LatestRequest();
  const closing = gate.begin();
  gate.cancel();
  assert.equal(closing.signal.aborted, true);
  assert.equal(closing.current(), false);
  const reopened = gate.begin();
  assert.equal(reopened.signal.aborted, false);
  assert.equal(reopened.current(), true);
  assert.equal(closing.current(), false);
});
