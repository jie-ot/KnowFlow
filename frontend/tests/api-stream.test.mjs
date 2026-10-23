import assert from "node:assert/strict";
import test from "node:test";
import { api, postSse, runAi } from "../src/lib/api.ts";

const event = (name, data) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

test("EOF without a terminal event preserves partial output and reports interruption", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(event("delta", { text: "partial" })));
  let text = "";
  let done = false;
  await assert.rejects(runAi({}, { onDelta: (part) => { text += part; }, onDone: () => { done = true; } }),
    { code: "STREAM_INTERRUPTED" });
  assert.equal(text, "partial");
  assert.equal(done, false);
});

test("chunked CRLF stream only finishes after its done event", async (t) => {
  const bytes = new TextEncoder().encode((event("delta", { text: "中文" }) + event("done", {})).replaceAll("\n", "\r\n"));
  t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); },
  })));
  const seen = [];
  await postSse("/test", {}, (name, data) => seen.push([name, data]));
  assert.deepEqual(seen, [["delta", { text: "中文" }], ["done", {}]]);
});

test("malformed event JSON is an explicit format failure", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response("event: done\ndata: {\n\n"));
  await assert.rejects(postSse("/test", {}, () => {}), { code: "STREAM_FORMAT" });
});

test("error event is terminal and is never converted to successful completion", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(event("error", { error: { kind: "TRUNCATED" }, partial: "half" })));
  const seen = [];
  await runAi({}, { onError: (error, partial) => seen.push([error.kind, partial]), onDone: () => assert.fail("false success") });
  assert.deepEqual(seen, [["TRUNCATED", "half"]]);
});

test("connection failure while reading is reported as incomplete", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    start(controller) { controller.error(new Error("disconnected")); },
  })));
  await assert.rejects(postSse("/test", {}, () => {}), { code: "STREAM_INTERRUPTED" });
});

test("dataset identity travels with writes and a replacement reloads the old window", async (t) => {
  let calls = 0;
  let reloaded = 0;
  globalThis.window = { location: { reload() { reloaded++; } } };
  t.after(() => { delete globalThis.window; });
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    if (calls === 2) assert.equal(new Headers(init.headers).get("X-KnowFlow-Dataset"), "dataset-1");
    return new Response("{}", { headers: { "Content-Type": "application/json", "X-KnowFlow-Dataset": calls < 3 ? "dataset-1" : "dataset-2" } });
  });
  await api("/api/notes");
  await api("/api/notes/n", { method: "PATCH", json: {} });
  await api("/api/notes");
  assert.equal(reloaded, 1);
});
