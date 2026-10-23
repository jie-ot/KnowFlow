import assert from "node:assert/strict";
import test from "node:test";
import { ReviewSubmission, FOCUS_KEY, MAX_FOCUS_MINUTES, clearFocusJournal, focusElapsed, freezeFocus,
  recoverFocus, refreshReviewQueue, restoreFocusSession, storeFocus } from "../src/lib/submission.ts";

class MemoryStorage {
  values = new Map();
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}
const card = { id: "card-1", review_revision: 0 };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("slow scoring, double clicks and stale callbacks advance exactly one card", async () => {
  const storage = new MemoryStorage();
  const submit = new ReviewSubmission(() => storage);
  const pending = deferred();
  let calls = 0, index = 0;
  const send = () => { calls++; return pending.promise; };
  const grade = async () => { if (await submit.submit(card, "good", send)) index++; };
  const first = grade();
  await Promise.all([grade(), grade(), grade()]);
  assert.equal(calls, 1);
  assert.equal(index, 0);
  pending.resolve({});
  await first;
  await grade(); // a stale handler from the previous render
  assert.equal(index, 1);
  assert.equal(calls, 1);
  assert.equal(storage.values.size, 0);
});

test("a lost scoring response retains original operation and grade across refresh", async () => {
  const storage = new MemoryStorage();
  const first = new ReviewSubmission(() => storage);
  const requests = [];
  await assert.rejects(first.submit(card, "forgot", async (request) => {
    requests.push(request);
    throw new Error("response lost after server commit");
  }));
  const restored = new ReviewSubmission(() => storage);
  assert.equal(restored.pendingFor(card).grade, "forgot");
  assert.equal(await restored.submit(card, "good", async (request) => { requests.push(request); }), true);
  assert.deepEqual(requests[1], requests[0], "retry freezes grade, operation ID and expected revision");
  assert.equal(storage.values.size, 0);
});

test("new scoring revision allows a legal new round with a fresh operation ID", async () => {
  const submit = new ReviewSubmission(() => new MemoryStorage());
  const requests = [];
  const send = async (request) => { requests.push(request); };
  assert.equal(await submit.submit(card, "good", send), true);
  assert.equal(await submit.submit({ ...card, review_revision: 1 }, "hard", send), true);
  assert.notEqual(requests[0].operation_id, requests[1].operation_id);
  assert.equal(requests[1].expected_revision, 1);
});

test("clearing a settled review does not erase another window's pending operation", async () => {
  const storage = new MemoryStorage();
  const submit = new ReviewSubmission(() => storage);
  await submit.submit(card, "good", async () => {
    const key = [...storage.values.keys()][0];
    storage.setItem(key, JSON.stringify({ grade: "hard", operation_id: "other-window", expected_revision: 0 }));
  });
  assert.equal(JSON.parse([...storage.values.values()][0]).operation_id, "other-window");
});

const session = { id: "session-1", taskId: "task", planId: "plan", title: "Study", startedAt: "2026-10-23T10:00:00Z",
  durationMin: 25, runMs: 60000, resumedAt: 100000 };

test("failed focus submission survives refresh with frozen minutes and completion choice", () => {
  const frozen = freezeFocus(session, true, 220000);
  assert.equal(frozen.resumedAt, null);
  assert.equal(frozen.submission.minutes, 3);
  const restored = restoreFocusSession(JSON.stringify(frozen));
  assert.deepEqual(restored, frozen);
  assert.equal(focusElapsed(restored, 999999999), 180000, "retry waiting time is not charged as focus");
  assert.deepEqual(freezeFocus(restored, false, 999999999), frozen, "a retry cannot change the recorded snapshot");
});

test("legacy focus state upgrades deterministically without resetting elapsed time", () => {
  const old = { ...session };
  delete old.id;
  const left = restoreFocusSession(JSON.stringify(old));
  const right = restoreFocusSession(JSON.stringify(old));
  assert.equal(left.id, right.id);
  assert.equal(left.runMs, old.runMs);
  assert.equal(left.resumedAt, old.resumedAt);
  assert.equal(freezeFocus(left, false, 220000).submission.session_id, left.id);
});

test("corrupt stored focus requests are rejected and clock reversal does not subtract work", () => {
  assert.equal(restoreFocusSession("not json"), null);
  assert.equal(restoreFocusSession(JSON.stringify({ ...session, runMs: -1 })), null);
  const frozen = freezeFocus(session, false, 220000);
  frozen.submission.session_id = "another session";
  assert.equal(restoreFocusSession(JSON.stringify(frozen)), null);
  assert.equal(focusElapsed(session, 50000), 60000);
});

test("a stale window cannot discard, pause or overwrite another window's frozen focus journal", () => {
  const storage = new MemoryStorage();
  storeFocus(storage, session, "window-A", null);
  const staleB = { ...session };
  const frozen = freezeFocus(session, true, 220000);
  storeFocus(storage, frozen, "window-A", session);
  assert.deepEqual(storeFocus(storage, null, "window-B", staleB), frozen);
  assert.deepEqual(storeFocus(storage, { ...staleB, runMs: 999000, resumedAt: null }, "window-B", staleB), frozen);
  // Even a legacy window, which knows nothing about the journal, can only
  // delete/overwrite the old running key. Refresh recovers the immutable intent.
  storage.removeItem(FOCUS_KEY);
  assert.deepEqual(recoverFocus(storage), frozen);
  storage.setItem(FOCUS_KEY, JSON.stringify(staleB));
  assert.deepEqual(recoverFocus(storage), frozen);
});

test("concurrent focus snapshots retain both journals until confirmed or explicitly reviewed", () => {
  const storage = new MemoryStorage();
  const first = freezeFocus(session, true, 220000);
  const competing = freezeFocus(session, false, 280000);
  storeFocus(storage, first, "A", session);
  storeFocus(storage, competing, "B", session);
  assert.equal([...storage.values.keys()].filter((key) => key.startsWith("kf-focus-pending-v1:")).length, 2);
  const remaining = clearFocusJournal(storage, first);
  assert.deepEqual(remaining, competing, "confirming one payload does not silently erase the other snapshot");
  assert.deepEqual(recoverFocus(storage), competing);
  assert.equal(clearFocusJournal(storage, competing, true), null, "explicit reviewed cleanup can clear the remaining local snapshot");
});

test("overnight focus keeps all minutes and over-limit data remains editable and exportable", () => {
  const long = { ...session, runMs: 0, resumedAt: 0 };
  assert.equal(freezeFocus(long, true, 601 * 60000).submission.minutes, 601);
  assert.equal(freezeFocus(long, true, MAX_FOCUS_MINUTES * 60000).submission.minutes, MAX_FOCUS_MINUTES);
  const before = { ...long };
  assert.throws(() => freezeFocus(long, false, (MAX_FOCUS_MINUTES + 1) * 60000), /31/);
  assert.deepEqual(long, before);
  assert.equal(long.submission, undefined, "invalid over-limit submission is never frozen");
});

test("checking for another review round waits for fresh data and preserves the finished page on failure", async () => {
  const finished = { items: [{ id: "stale-reviewed-card" }] };
  let state = finished;
  const pending = deferred();
  const refreshing = refreshReviewQueue(() => pending.promise, (fresh) => { state = fresh; });
  assert.equal(state, finished, "do not reset to null and let old SWR cache repopulate");
  pending.resolve({ items: [] });
  await refreshing;
  assert.deepEqual(state, { items: [] });
  state = finished;
  await assert.rejects(refreshReviewQueue(async () => { throw new Error("offline"); }, (fresh) => { state = fresh; }));
  assert.equal(state, finished);
});
