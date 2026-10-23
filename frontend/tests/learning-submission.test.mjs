import assert from "node:assert/strict";
import test from "node:test";
import { emptyStudyDraft, pendingRecoveryView, forgetStudyDraft, settledLearningTask, settledUnderstanding, storeStudyDraft, studyDraftText, studyDrafts,
  StudyPendingChanged, StudySubmission } from "../src/lib/learning-submission.ts";

class MemoryStorage {
  values = new Map();
  get length() { return this.values.size; }
  key(i) { return [...this.values.keys()][i] ?? null; }
  getItem(k) { return this.values.get(k) ?? null; }
  setItem(k, v) { this.values.set(k, v); }
  removeItem(k) { this.values.delete(k); }
}

test("lost study response survives refresh and freezes original payload and ID", async () => {
  const storage = new MemoryStorage(), requests = [], receipts = new Map();
  let results = 0;
  const server = async (request) => {
    requests.push(request);
    if (!receipts.has(request.operation_id)) receipts.set(request.operation_id, { id: ++results });
    if (requests.length === 1) throw { status: 0, message: "response lost after commit" };
    return receipts.get(request.operation_id);
  };
  const first = new StudySubmission("card", "task", () => storage);
  await assert.rejects(first.submit({ title: "original", plan_id: "plan" }, server));
  const restored = new StudySubmission("card", "task", () => storage);
  assert.equal(restored.pending().title, "original");
  assert.deepEqual(await restored.submit({ title: "changed" }, server, restored.pending().operation_id), { id: 1 });
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(results, 1);
  assert.equal(storage.length, 0);
});

test("rapid double submit produces a single request and separate journals preserve other windows", async () => {
  const storage = new MemoryStorage(), submit = new StudySubmission("card", "understanding", () => storage);
  let resolve, calls = 0;
  const wait = new Promise((yes) => { resolve = yes; });
  const first = submit.submit({ text: "one", base_version: 1 }, async () => { calls++; return wait; });
  assert.equal(await submit.submit({ text: "two" }, async () => { calls++; }), undefined);
  const otherKey = "kf-study-operation-v1:card:understanding:other-window-id";
  storage.setItem(otherKey, JSON.stringify({ operation_id: "other-window-id", text: "other", base_version: 1 }));
  resolve({ version: 2 });
  await first;
  assert.equal(calls, 1);
  assert.equal(JSON.parse(storage.getItem(otherKey)).text, "other");
  assert.equal(submit.pending().text, "other");
});

test("a definite conflict unlocks request while caller retains text; storage failure sends nothing", async () => {
  const storage = new MemoryStorage(), submit = new StudySubmission("card", "understanding", () => storage);
  const draft = { text: "keep my understanding", base_version: 1 };
  await assert.rejects(submit.submit(draft, async () => { throw { status: 409, code: "VERSION_CONFLICT" }; }));
  assert.equal(submit.pending(), null);
  assert.equal(draft.text, "keep my understanding");
  let calls = 0;
  storage.setItem = () => { throw new Error("full"); };
  await assert.rejects(submit.submit(draft, async () => { calls++; }));
  assert.equal(calls, 0);
});

test("a journal appearing in another window stops submission until its exact original request is displayed", async () => {
  const storage = new MemoryStorage();
  const windowA = new StudySubmission("card", "understanding", () => storage);
  assert.equal(windowA.pending(), null);
  const ownDraft = { ...emptyStudyDraft(), text: "Window A understanding", baseVersion: 1 };
  const windowB = new StudySubmission("card", "understanding", () => storage);
  await assert.rejects(windowB.submit({ text: "Window B understanding", base_version: 1 }, async () => { throw { status: 0 }; }));
  let requests = 0;
  await assert.rejects(windowA.submit({ text: ownDraft.text, base_version: 1 }, async () => { requests++; }), StudyPendingChanged);
  assert.equal(requests, 0, "an unseen operation must never replace the displayed draft during send");
  assert.equal(ownDraft.text, "Window A understanding");
  const displayed = windowA.pending();
  assert.equal(displayed.text, "Window B understanding");
  await windowA.submit({ text: ownDraft.text, base_version: 1 }, async (request) => {
    requests++;
    assert.deepEqual(request, displayed);
    return { version: 2 };
  }, displayed.operation_id);
  const settled = settledUnderstanding(ownDraft, displayed, 2);
  assert.equal(settled.text, "Window A understanding", "confirming B must preserve A's separate input");
  assert.equal(settled.baseVersion, 1, "A must explicitly refresh before rebasing");
  assert.equal(settled.needsRefresh, true);
  assert.equal(requests, 1);
});

test("window drafts remain separate across refresh and explicit recovery never erases other copies", () => {
  const storage = new MemoryStorage();
  const a = { ...emptyStudyDraft(), text: "A explanation", baseVersion: 1 };
  const b = { ...emptyStudyDraft(), text: "B explanation", baseVersion: 2 };
  storeStudyDraft(storage, "card", "window-A", a);
  storeStudyDraft(storage, "card", "window-B", b);
  let recovered = studyDrafts(storage, "card", "refreshed-window");
  assert.deepEqual(new Set(recovered.map((r) => r.value.text)), new Set([a.text, b.text]));
  const displayedA = recovered.find((r) => r.value.text === a.text);
  storeStudyDraft(storage, "card", "refreshed-window", displayedA.value); // explicit restore action
  assert.equal(studyDrafts(storage, "card", "refreshed-window").length, 2);
  storeStudyDraft(storage, "card", "window-A", { ...a, text: "A kept typing" });
  forgetStudyDraft(storage, displayedA);
  recovered = studyDrafts(storage, "card", "refreshed-window");
  assert.equal(recovered.find((r) => r.key === displayedA.key).value.text, "A kept typing", "stale cleanup cannot remove new text");
  storeStudyDraft(storage, "card", "window-B", emptyStudyDraft());
  assert.equal(studyDrafts(storage, "card", "refreshed-window").length, 1, "clearing B only clears B");
});

test("legacy drafts remain explicitly recoverable and task confirmation clears only matching forms", () => {
  const storage = new MemoryStorage();
  const draft = { ...emptyStudyDraft(), text: "legacy input", title: "Task A", plan: "plan-A", due: "", minutes: "25", taskEdited: true };
  storage.setItem("kf-weak-study-draft-v1:card", JSON.stringify(draft));
  assert.equal(studyDrafts(storage, "card", "new-window")[0].value.text, "legacy input");
  const other = { operation_id: "another-window", title: "Task B", plan_id: "plan-B", due_date: null, estimated_minutes: 25 };
  assert.equal(settledLearningTask(draft, other).title, "Task A");
  const own = { ...other, title: "Task A", plan_id: "plan-A" };
  assert.equal(settledLearningTask(draft, own).title, "");
  assert.equal(settledLearningTask(draft, own).text, "legacy input", "task confirmation never clears understanding");
});

test("a rejected foreign operation is retained if preserving its editable recovery copy fails", async () => {
  const storage = new MemoryStorage(), submit = new StudySubmission("card", "understanding", () => storage);
  await assert.rejects(submit.submit({ text: "foreign explanation", base_version: 1 }, async () => { throw { status: 0 }; }));
  const pending = submit.pending();
  await assert.rejects(submit.submit({}, async () => { throw { status: 409, code: "VERSION_CONFLICT" }; }, pending.operation_id,
    () => { throw new Error("recovery disk full"); }), /recovery disk full/);
  assert.deepEqual(submit.pending(), pending, "do not delete the only remaining copy before recovery is durable");
  let recovered;
  await assert.rejects(submit.submit({}, async () => { throw { status: 409, code: "VERSION_CONFLICT" }; }, pending.operation_id,
    (request) => { recovered = request; }));
  assert.deepEqual(recovered, pending);
  assert.equal(submit.pending(), null);
});

test("pending recovery view stays available when card detail is unavailable", () => {
  const view = pendingRecoveryView({ operation_id: "understanding-123", text: "remember this", base_version: 4 },
    { operation_id: "task-456", title: "revisit", plan_id: null });
  assert.equal(view.length, 2);
  assert.equal(view[0].operationId, "understanding-123");
  assert.equal(view[0].text, "remember this");
  assert.equal(view[1].title, "revisit");
});

test("detail 404 does not block retrying an original study receipt after source deletion", async () => {
  const storage = new MemoryStorage(), first = new StudySubmission("card", "task", () => storage);
  let receipt, calls = 0;
  await assert.rejects(first.submit({ title: "task before deletion", plan_goal: "Plan", due_date: null, estimated_minutes: 25 }, async (request) => {
    receipt = { request, response: { id: "task-result", plan_id: "plan" } };
    throw { status: 0 };
  }));
  const detail = undefined; // GET returns 404 because note deletion also trashed the card.
  assert.equal(detail, undefined);
  const restored = new StudySubmission("card", "task", () => storage);
  const shown = pendingRecoveryView(null, restored.pending())[0];
  const result = await restored.submit({}, async (request) => {
    calls++;
    assert.deepEqual(request, receipt.request, "receipt lookup must use the original ID and payload");
    return receipt.response;
  }, shown.operationId);
  assert.deepEqual(result, receipt.response);
  assert.equal(calls, 1);
  assert.equal(restored.pending(), null);
});

test("offline recovery export retains full understanding and task fields without truncation", () => {
  const text = "草稿\n".repeat(3000);
  const exported = studyDraftText({ ...emptyStudyDraft(), text, title: "Read chapter", taskEdited: true,
    goal: "Improve", due: "2099-01-01", minutes: "30" });
  assert.ok(exported.includes(text));
  assert.match(exported, /Read chapter/);
  assert.match(exported, /2099-01-01/);
  assert.match(exported, /30/);
});
