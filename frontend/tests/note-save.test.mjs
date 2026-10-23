import assert from "node:assert/strict";
import test from "node:test";
import { NoteSaveCoordinator, readNoteDrafts } from "../src/lib/note-save.ts";

class MemoryStorage {
  values = new Map();
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}
const note = (overrides = {}) => ({ title: "Original", content: "Original body", tags: [], course_id: "course", version: 1, ...overrides });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function setup(save, options = {}) {
  const storage = options.storage ?? new MemoryStorage();
  const queue = new NoteSaveCoordinator(note(), { noteId: "note", owner: "window-A", storage, save, ...options });
  return { queue, storage };
}
const conflict = () => Object.assign(new Error("Version conflict"), { status: 409, code: "VERSION_CONFLICT" });

test("slow saves serialize requests and coalesce new edits without replacing the editor", async () => {
  const first = deferred();
  const second = deferred();
  const calls = [];
  const { queue, storage } = setup((fields, baseVersion) => {
    calls.push({ fields, baseVersion });
    return calls.length === 1 ? first.promise : second.promise;
  });
  queue.edit({ content: "first" }, false);
  const saving = queue.flush();
  queue.edit({ content: "second" }, false);
  queue.edit({ content: "latest", title: "Latest title" }, false);
  assert.equal(queue.flush(), saving, "manual save shares the in-flight queue");
  assert.equal(calls.length, 1);
  first.resolve(note({ content: "first", version: 2 }));
  await new Promise(setImmediate);
  assert.equal(queue.getSnapshot().fields.content, "latest");
  assert.equal(queue.getSnapshot().fields.title, "Latest title");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].fields.content, "latest");
  assert.equal(calls[1].baseVersion, 2);
  assert.equal(readNoteDrafts(storage, "note")[0].fields.content, "latest");
  second.resolve(note({ ...calls[1].fields, version: 3 }));
  assert.equal(await saving, true);
  assert.equal(queue.getSnapshot().status, "saved");
  assert.equal(storage.length, 0);
});

test("reverting to the old value during an in-flight save remains durable and is sent next", async () => {
  const first = deferred();
  const calls = [];
  const { queue, storage } = setup((fields, version) => {
    calls.push(fields.content);
    return calls.length === 1 ? first.promise : Promise.resolve(note({ ...fields, version: version + 1 }));
  });
  queue.edit({ content: "transient" }, false);
  const saving = queue.flush();
  queue.edit({ content: "Original body" }, false);
  assert.equal(readNoteDrafts(storage, "note")[0].fields.content, "Original body");
  first.resolve(note({ content: "transient", version: 2 }));
  assert.equal(await saving, true);
  assert.deepEqual(calls, ["transient", "Original body"]);
});

test("a failed request after switching editors retains the latest draft and can retry", async () => {
  const pending = deferred();
  let attempts = 0;
  const { queue, storage } = setup((fields, version) => ++attempts === 1 ? pending.promise : Promise.resolve(note({ ...fields, version: version + 1 })));
  const unsubscribe = queue.subscribe(() => {});
  queue.edit({ content: "unsaved" }, false);
  const saving = queue.flush();
  unsubscribe(); // Switching notes removes the UI subscriber, not its save state.
  queue.edit({ content: "latest unsaved" }, false);
  pending.reject(new Error("offline"));
  assert.equal(await saving, false);
  assert.equal(queue.getSnapshot().status, "error");
  assert.equal(queue.getSnapshot().localSaved, true);
  assert.equal(readNoteDrafts(storage, "note")[0].fields.content, "latest unsaved");
  assert.equal(await queue.flush(), true);
  assert.equal(queue.getSnapshot().saved.content, "latest unsaved");
});

test("refresh restores a draft immediately and waits for confirmation before synchronizing", async () => {
  const storage = new MemoryStorage();
  const { queue } = setup(async () => { throw new Error("must not run"); }, { storage });
  queue.edit({ content: "survives refresh", tags: ["tag"] }, false);
  let calls = 0;
  const restored = new NoteSaveCoordinator(note(), { noteId: "note", owner: "new-window", storage,
    save: async (fields, version) => { calls++; return note({ ...fields, version: version + 1 }); } });
  assert.equal(restored.getSnapshot().fields.content, "survives refresh");
  assert.equal(restored.getSnapshot().recovered, true);
  assert.equal(calls, 0);
  assert.equal(await restored.flush(), true);
  assert.equal(calls, 1);
  assert.equal(storage.length, 0, "successful recovery removes the adopted draft too");
});

test("multiple windows retain separate drafts and concurrent saves surface a conflict", async () => {
  const storage = new MemoryStorage();
  let remote = note();
  const save = async (fields, version) => {
    if (version !== remote.version) throw conflict();
    remote = note({ ...fields, version: version + 1 });
    return remote;
  };
  const a = new NoteSaveCoordinator(remote, { noteId: "note", owner: "A", storage, save });
  const b = new NoteSaveCoordinator(remote, { noteId: "note", owner: "B", storage, save });
  a.edit({ content: "A content" }, false);
  b.edit({ content: "B content" }, false);
  assert.equal(readNoteDrafts(storage, "note").length, 2);
  const reopened = new NoteSaveCoordinator(remote, { noteId: "note", owner: "C", storage, save });
  assert.equal(reopened.getSnapshot().drafts.length, 2);
  assert.equal(reopened.getSnapshot().recovered, false, "ambiguous drafts are never chosen silently");
  assert.equal(await a.flush(), true);
  assert.equal(await b.flush(), false);
  assert.equal(b.getSnapshot().status, "conflict");
  assert.equal(b.getSnapshot().fields.content, "B content");
  assert.equal(readNoteDrafts(storage, "note")[0].fields.content, "B content");
  assert.equal(b.resolve(remote, { ...b.getSnapshot().fields, content: "A content\nB content" }), true);
  assert.equal(await b.flush(), true);
  assert.equal(remote.content, "A content\nB content");
});

test("remote refresh never replaces pending edits and resolution uses the newest version", async () => {
  const calls = [];
  const { queue } = setup(async (fields, version) => { calls.push(version); return note({ ...fields, version: version + 1 }); });
  queue.edit({ content: "local" }, false);
  queue.receiveRemote(note({ title: "remote title", content: "remote", version: 4 }));
  assert.equal(queue.getSnapshot().status, "conflict");
  assert.equal(queue.getSnapshot().fields.content, "local");
  assert.equal(queue.getSnapshot().base.content, "Original body");
  assert.equal(await queue.flush(), false);
  assert.deepEqual(calls, []);
  queue.resolve(note({ title: "remote title", content: "remote", version: 4 }), { ...queue.getSnapshot().fields, title: "remote title" });
  assert.equal(await queue.flush(), true);
  assert.deepEqual(calls, [4]);
});

test("recovered conflicts keep their original comparison base across another refresh", () => {
  const storage = new MemoryStorage();
  const { queue } = setup(async () => note(), { storage });
  queue.edit({ content: "local from version 1" }, false);
  const options = { noteId: "note", owner: "restored", storage, save: async () => note() };
  const restored = new NoteSaveCoordinator(note({ content: "server v9", version: 9 }), options);
  assert.equal(restored.getSnapshot().status, "conflict");
  assert.equal(restored.getSnapshot().base.content, "Original body");
  const written = readNoteDrafts(storage, "note").find((draft) => draft.owner === "restored");
  assert.equal(written.baseVersion, 1);
  assert.equal(written.base.content, "Original body");
});

test("adopts backend normalization only when the corresponding field has not changed", async () => {
  const pending = deferred();
  let requests = 0;
  const { queue } = setup(async (fields, version) => ++requests === 1 ? pending.promise : note({ ...fields, version: version + 1 }));
  queue.edit({ title: "  hello  ", content: "first" }, false);
  const saving = queue.flush();
  queue.edit({ content: "new input" }, false);
  pending.resolve(note({ title: "hello", content: "first", version: 2 }));
  assert.equal(await saving, true);
  assert.equal(queue.getSnapshot().fields.title, "hello");
  assert.equal(queue.getSnapshot().fields.content, "new input");
});

test("storage failure is explicit and does not prevent backend synchronization", async () => {
  const storage = new MemoryStorage();
  storage.setItem = () => { throw new Error("quota exceeded"); };
  const { queue } = setup(async (fields, version) => note({ ...fields, version: version + 1 }), { storage });
  queue.edit({ content: "unpersisted" }, false);
  assert.equal(queue.getSnapshot().localSaved, false);
  assert.equal(queue.getSnapshot().localError, "quota exceeded");
  assert.equal(await queue.flush(), true);
  assert.equal(queue.getSnapshot().status, "saved");
});

test("loading another window's draft archives this window's current changes", () => {
  const storage = new MemoryStorage();
  const { queue: source } = setup(async () => note(), { storage, owner: "source" });
  source.edit({ content: "source" }, false);
  const { queue } = setup(async () => note(), { storage, owner: "target" });
  queue.edit({ content: "my own changes" }, false);
  queue.recoverDraft(readNoteDrafts(storage, "note").find((draft) => draft.owner === "source"));
  assert.equal(queue.getSnapshot().fields.content, "source");
  assert.ok(readNoteDrafts(storage, "note").some((draft) => draft.fields.content === "my own changes"));
});

test("syncing an adopted draft does not delete subsequent edits made by its live source window", async () => {
  const storage = new MemoryStorage();
  const pending = deferred();
  const { queue: source } = setup(async () => note(), { storage, owner: "source" });
  source.edit({ content: "adopted" }, false);
  const { queue } = setup(async () => pending.promise, { storage, owner: "recovery" });
  const saving = queue.flush();
  source.edit({ content: "new live-window edit" }, false);
  pending.resolve(note({ content: "adopted", version: 2 }));
  assert.equal(await saving, true);
  assert.ok(readNoteDrafts(storage, "note").some((draft) => draft.fields.content === "new live-window edit"));
});

test("invalid persisted entries do not hide valid drafts or crash recovery", () => {
  const storage = new MemoryStorage();
  const { queue } = setup(async () => note(), { storage });
  queue.edit({ content: "valid" }, false);
  storage.setItem("kf-note-draft-v1:note:broken", "{not json");
  storage.setItem("kf-note-draft-v1:note:bad-types", JSON.stringify({ schema: 1, noteId: "note", owner: "invalid", baseVersion: 1, fields: { title: false } }));
  assert.equal(readNoteDrafts(storage, "note").length, 1);
});

test("different notes can synchronize independently while a slow note stays in flight", async () => {
  const pending = deferred();
  const { queue: slow } = setup(async () => pending.promise);
  const { queue: other } = setup(async (fields, version) => note({ ...fields, version: version + 1 }), { noteId: "other" });
  slow.edit({ content: "slow" }, false);
  const saving = slow.flush();
  other.edit({ content: "other" }, false);
  assert.equal(await other.flush(), true);
  assert.equal(slow.getSnapshot().status, "saving");
  pending.resolve(note({ content: "slow", version: 2 }));
  assert.equal(await saving, true);
});

test("repeated refreshes restore equivalent drafts once and clean their unchanged copies", async () => {
  const storage = new MemoryStorage();
  const save = async (fields, version) => note({ ...fields, version: version + 1 });
  const first = new NoteSaveCoordinator(note(), { noteId: "note", owner: "first", storage, save });
  first.edit({ content: "survives two refreshes" }, false);
  new NoteSaveCoordinator(note(), { noteId: "note", owner: "second", storage, save });
  const third = new NoteSaveCoordinator(note(), { noteId: "note", owner: "third", storage, save });
  assert.equal(third.getSnapshot().recovered, true);
  assert.equal(third.getSnapshot().fields.content, "survives two refreshes");
  assert.equal(await third.flush(), true);
  assert.equal(storage.length, 0);
});

test("same-version replacement from a restored database cannot overwrite pending local content", () => {
  const { queue } = setup(async () => note());
  queue.edit({ content: "pending local content" }, false);
  queue.receiveRemote(note({ content: "restored database contents", version: 1 }));
  assert.equal(queue.getSnapshot().status, "conflict");
  assert.equal(queue.getSnapshot().fields.content, "pending local content");
});

test("recovery detects restored database changes even when the version number is unchanged", () => {
  const storage = new MemoryStorage();
  const { queue } = setup(async () => note(), { storage });
  queue.edit({ content: "local draft" }, false);
  const restored = new NoteSaveCoordinator(note({ content: "different restored content", version: 1 }), {
    noteId: "note", owner: "after-restore", storage, save: async () => note(),
  });
  assert.equal(restored.getSnapshot().status, "conflict");
  assert.equal(restored.getSnapshot().fields.content, "local draft");
  assert.equal(restored.getSnapshot().base.content, "Original body");
});

test("debounced edits automatically synchronize only the latest edit", async () => {
  const calls = [];
  const complete = deferred();
  const { queue } = setup(async (fields, version) => {
    calls.push(fields.content);
    complete.resolve();
    return note({ ...fields, version: version + 1 });
  }, { delay: 5 });
  queue.edit({ content: "first" });
  queue.edit({ content: "latest" });
  await complete.promise;
  await queue.flush();
  assert.deepEqual(calls, ["latest"]);
});
